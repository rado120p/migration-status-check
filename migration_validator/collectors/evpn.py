"""Sber EVPN stavu pro E-Line (vpws) a E-LAN (vlan-aware, vlan-based).

Platformni rozdil se resi tady. Navenek vraci obe platformy stejne schema,
takze check evpn_mac_count nikde neobsahuje vetev na platformu.

RPC nazvy jsou overene proti laborce pres '| display xml rpc'. Pozor, plan
uvadel jmena, ktera na zadne z obou platforem neexistuji:

    get_evpn_vpws_instance_information   -> spravne get_evpn_vpws_information
    get_mac_vrf_forwarding_mac_table     -> spravne get_mac_vrf_mac_table

MAC tabulka potrebuje na MX dve RPC, protoze kazde vidi jiny typ instance:
'show bridge mac-table' vraci jen vlan-aware instance, 'show evpn mac-table'
jen vlan-based. Slouceni je platformni normalizace, tedy prace collectoru.
"""

from __future__ import annotations

from typing import Any

from lxml import etree

from migration_validator.collectors.base import Collector, CollectorError
from migration_validator.collectors.interfaces import _int, _text
from migration_validator.collectors.registry import register


@register
class EvpnVpwsCollector(Collector):
    """Stav EVPN-VPWS instanci vcetne vsech rozhrani a peeru obou SID.

    Puvodne se ctlo jen prvni rozhrani a dve cisla SID -
    'evpn-vpws-sid-pe-status-table' (podstata checku evpn_vpws_status) se
    ignorovala. Nove schema nese vsechna rozhrani instance a u kazdeho SID
    seznam peeru s jejich statusem.
    """

    name = "evpn_vpws"

    def rpc_name(self, platform: str) -> str:
        return "get_evpn_vpws_information"

    def parse(self, xml: etree._Element, platform: str) -> dict[str, dict[str, Any]]:
        instances: dict[str, dict[str, Any]] = {}
        for node in xml.iter("evpn-vpws-instance"):
            name = _text(node, "evpn-vpws-instance-name")
            if not name:
                continue
            interfaces = [
                self._interface(iface)
                for iface in node.iter("evpn-vpws-interface")
            ]
            instances[name] = {"interfaces": interfaces}
        return instances

    def _interface(self, iface: etree._Element) -> dict[str, Any]:
        return {
            "name": _text(iface, "evpn-vpws-interface-name"),
            "status": _text(iface, "evpn-vpws-interface-status") or "unknown",
            "mode": _text(iface, "evpn-vpws-interface-mode"),
            "local_sid": self._sid(
                iface,
                "evpn-vpws-service-id-local-status-table/evpn-vpws-sid-local",
                "evpn-vpws-sid-local-value",
            ),
            "remote_sid": self._sid(
                iface,
                "evpn-vpws-service-id-remote-status-table/evpn-vpws-sid-remote",
                "evpn-vpws-sid-remote-value",
            ),
        }

    @staticmethod
    def _sid(iface: etree._Element, path: str, value_tag: str) -> dict[str, Any]:
        sid = iface.find(path)
        if sid is None:
            return {"value": None, "peers": []}
        peers = [
            {
                "esi": _text(peer, "evpn-vpws-sid-interface-esi"),
                "ipaddr": _text(peer, "evpn-vpws-sid-pe-ipaddr"),
                "mode": _text(peer, "evpn-vpws-sid-pe-mode"),
                "role": _text(peer, "evpn-vpws-sid-pe-role"),
                "status": _text(peer, "evpn-vpws-sid-pe-status"),
            }
            for peer in sid.iter("evpn-vpws-sid-pe-info")
        ]
        return {"value": _int(sid, value_tag), "peers": peers}


@register
class EvpnEsiCollector(Collector):
    """Stav ethernet segmentu (multihoming).

    ESI bloky jsou v odpovedi jen s 'extensive' - bez nej 'show evpn instance'
    vrati souhrn bez jedineho ESI a collector by tise vracel prazdno.

    'interface' je zamerne logicka jednotka ('ae0.14'), protoze presne tu drzi
    scope v selektorech. Junos ji tady dava rovnou ve spravnem tvaru, takze
    obava ze specu (fyzicky rodic bez Layer1 zaznamu) tady nenastava.
    """

    name = "evpn_esi"

    def rpc_name(self, platform: str) -> str:
        return "get_evpn_instance_information"

    def rpc_kwargs(self, platform: str) -> dict[str, Any]:
        return {"extensive": True}

    def parse(self, xml: etree._Element, platform: str) -> dict[str, dict[str, Any]]:
        segments: dict[str, dict[str, Any]] = {}

        for node in xml.iter("evpn-esi"):
            esi = _text(node, "evpn-esi-value")
            if not esi:
                continue

            # ESI zacinajici 05: si box generuje sam (per-IRB). Nenesou
            # status ani DF a v reportu by kazda L3-extended sluzba
            # svitila radkem bez vypovedi.
            if esi.startswith("05:"):
                continue

            local = node.find("evpn-esi-local-intf-information")
            df = node.find("evpn-esi-df-information")

            segments[esi] = {
                # 'Up/Forwarding' - stav lokalniho rozhrani v segmentu.
                # evpn-esi-status je proti tomu popisny text ('Resolved by
                # IFL ae0.14'), ktery se neda porovnavat.
                "status": _text(local, "evpn-esi-local-intf-status") or "unknown",
                # IP adresa zvoleneho DF, ne role tohohle boxu - urcit "jsem
                # DF?" by znamenalo interpretovat, a to collectoru nepatri.
                "df_role": _text(df, "esi-designated-forwarder"),
                "interface": _text(local, "evpn-esi-local-intf-name"),
            }

        return segments


@register
class EvpnInstanceCollector(Collector):
    """Per-instance stav EVPN: local/IRB rozhrani, neighbors, ESI.

    Tentyz extensive vypis jako EvpnEsiCollector, ale jina osa: tady je
    jednotkou instance (RI), tam ethernet segment napric instancemi.
    Na EVO je kanonicky prikaz 'show mac-vrf routing instance extensive'
    (RPC get_mac_vrf_instance_information, overeno v laborce) - odpoved
    ma shodny tvar evpn-instance-information jako MX, takze parse()
    nepotrebuje platformni vetev.
    """

    name = "evpn_instance"

    RPC_NAMES = {
        "junos": "get_evpn_instance_information",
        "junos-evo": "get_mac_vrf_instance_information",
    }

    # Interni instance boxu - neni sluzba, nema local interfaces a check
    # by na ni v device scope trvale hlasil FAIL.
    SYSTEM_INSTANCES = frozenset({"__default_evpn__"})

    def rpc_name(self, platform: str) -> str:
        return self.RPC_NAMES[platform]

    def rpc_kwargs(self, platform: str) -> dict[str, Any]:
        return {"extensive": True}

    def parse(self, xml: etree._Element, platform: str) -> dict[str, dict[str, Any]]:
        instances: dict[str, dict[str, Any]] = {}
        for node in xml.iter("evpn-instance"):
            name = _text(node, "evpn-instance-name")
            if not name or name in self.SYSTEM_INSTANCES:
                continue

            esis: dict[str, str] = {}
            for esi_node in node.iter("evpn-esi"):
                esi = _text(esi_node, "evpn-esi-value")
                # 05: ESI si box generuje sam - bez statusu, do reportu
                # nepatri (stejne pravidlo jako EvpnEsiCollector).
                if not esi or esi.startswith("05:"):
                    continue
                esis[esi] = _text(esi_node, "evpn-esi-status") or ""

            instances[name] = {
                "local_interfaces": {
                    "total": _int(node, "local-interfaces") or 0,
                    "up": _int(node, "local-interfaces-up") or 0,
                    "entries": [
                        {
                            "name": _text(iface, "evpn-interface-name"),
                            "status": _text(iface, "evpn-interface-status")
                            or "unknown",
                        }
                        for iface in node.iter("evpn-interface")
                    ],
                },
                "irb_interfaces": {
                    "total": _int(node, "irb-interfaces") or 0,
                    "up": _int(node, "irb-interfaces-up") or 0,
                    # Filtr na irb-interface-name: vypis obsahuje i hola
                    # <irb-interface>irb.14</irb-interface> pod bridge
                    # domenou, ktera zadne deti nemaji.
                    "entries": [
                        {
                            "name": _text(iface, "irb-interface-name"),
                            "status": _text(iface, "irb-interface-status")
                            or "unknown",
                            "l3_context": _text(iface, "irb-interface-l3-context"),
                        }
                        for iface in node.iter("irb-interface")
                        if iface.find("irb-interface-name") is not None
                    ],
                },
                "neighbors": {
                    "total": _int(node, "evpn-num-neighbors") or 0,
                    "addresses": [
                        element.text
                        for element in node.iter("evpn-neighbor-address")
                        if element.text
                    ],
                },
                "esis": esis,
            }
        return instances


@register
class EvpnMacCollector(Collector):
    """Pocty naucenych MAC adres z 'count' vypisu.

    Drive se stahovala cela MAC tabulka a pocitaly zaznamy - na boxu
    s tisici MAC to bylo drahe a per-interface pocty z toho nesly.
    'count' varianta tychz RPC vraci hotove pocty per learn-vlan a per
    interface.
    """

    name = "evpn_mac"

    # Poradi je zamerne: prvni je "hlavni" RPC pro `record`/`--record-raw`.
    # MX potrebuje dve RPC (bridge = vlan-aware, evpn = vlan-based),
    # EVO jedno. Uvadi se jen RPC, ktera na platforme opravdu plati.
    RPCS: dict[str, tuple[str, ...]] = {
        "junos": ("get_bridge_mac_table", "get_evpn_mac_table"),
        "junos-evo": ("get_mac_vrf_mac_table",),
    }

    # Systemove instance boxu - nejsou sluzba a v device scope by kazdy
    # beh svitily radkem bez vypovedi.
    SYSTEM_INSTANCES = frozenset({"default-switch"})

    def rpc_name(self, platform: str) -> str:
        return self.RPCS[platform][0]

    def rpc_names(self, platform: str) -> tuple[str, ...]:
        return self.RPCS[platform]

    def rpc_kwargs(self, platform: str) -> dict[str, Any]:
        return {"count": True}

    def collect(self, device: Any, platform: str) -> dict[str, dict[str, Any]]:
        # Stejny princip jako drive: selhani kterehokoliv RPC je chyba
        # celeho collectoru - castecna data by check porovnal proti plne
        # baseline a hlasil propad. Radsi SKIP nez tichy nesmysl.
        if not self.supports(platform):
            raise CollectorError(
                f"collector '{self.name}' nepodporuje platformu '{platform}'"
            )

        merged: dict[str, dict[str, Any]] = {}
        failures: list[str] = []

        for rpc_name in self.rpc_names(platform):
            try:
                xml = getattr(device.rpc, rpc_name)(**self.rpc_kwargs(platform))
            except Exception as error:  # noqa: BLE001
                failures.append(f"{rpc_name}: {type(error).__name__}: {error}")
                continue

            try:
                parsed = self.parse(xml, platform)
            except Exception as error:  # noqa: BLE001
                failures.append(f"{rpc_name}: parsovani selhalo - {error}")
                continue

            for instance, data in parsed.items():
                target = merged.setdefault(instance, {"vlans": {}, "interfaces": {}})
                for area in ("vlans", "interfaces"):
                    for key, entry in data[area].items():
                        slot = target[area].setdefault(key, dict(entry, count=0))
                        slot["count"] += entry["count"]

        if failures:
            raise CollectorError(
                f"collector '{self.name}': RPC selhalo - " + "; ".join(failures)
            )

        return merged

    # Tvary count vypisu. MX pouziva l2ald-* a domenu nazyva bd-name,
    # EVO l2ng-l2ald-* a vlan-name. Prochazi se oba, takze parse()
    # nepotrebuje vetev na platformu.
    #
    # (zaznam instance+domeny, nazev domeny, per-interface zaznam,
    #  per-vlan zaznam)
    COUNT_SHAPES: tuple[tuple[str, str, str, str], ...] = (
        (
            "l2ald-rtb-mac-count-entry",
            "bd-name",
            "l2ald-rtb-if-mac-count-entry",
            "l2ald-rtb-learn-vlan-mac-count-entry",
        ),
        (
            "l2ng-l2ald-rtb-mac-count-entry",
            "vlan-name",
            "l2ng-l2ald-rtb-if-mac-count-entry",
            "l2ng-l2ald-rtb-learn-vlan-mac-count-entry",
        ),
    )

    def parse(self, xml: etree._Element, platform: str) -> dict[str, dict[str, Any]]:
        instances: dict[str, dict[str, Any]] = {}

        for entry_tag, domain_tag, if_tag, vlan_tag in self.COUNT_SHAPES:
            for entry in xml.iter(entry_tag):
                instance = _text(entry, "rtb-name")
                if not instance or instance in self.SYSTEM_INSTANCES:
                    continue

                # Placeholder nazev (vlan-based: '__X__' na MX, 'VL-NONE'
                # na EVO) neni skutecna domena - klicem je vzdy learn-vlan,
                # nazev slouzi jen renderu ('BD-313 MAC count').
                raw_domain = _text(entry, domain_tag)
                domain = (
                    None
                    if raw_domain is None or _is_no_domain(raw_domain)
                    else raw_domain
                )

                target = instances.setdefault(
                    instance, {"vlans": {}, "interfaces": {}}
                )

                for vlan_entry in entry.iter(vlan_tag):
                    vlan = _text(vlan_entry, "learn-vlan")
                    count = _int(vlan_entry, "mac-count")
                    if vlan is None or count is None:
                        continue
                    slot = target["vlans"].setdefault(
                        vlan, {"count": 0, "domain": domain}
                    )
                    slot["count"] += count

                for if_entry in entry.iter(if_tag):
                    raw_name = _text(if_entry, "interface-name")
                    count = _int(if_entry, "mac-count")
                    # Prazdne <...-if-mac-count-entry/> bloky jsou ve
                    # vypisu bezne.
                    if not raw_name or count is None:
                        continue
                    # 'ge-0/0/2.313:313' -> klic 'ge-0/0/2.313': za
                    # dvojteckou je VLAN a selektory scope drzi jmeno bez ni.
                    key = raw_name.rsplit(":", 1)[0]
                    slot = target["interfaces"].setdefault(
                        key, {"count": 0, "name": raw_name, "domain": domain}
                    )
                    slot["count"] += count

        return instances


def _is_no_domain(name: str) -> bool:
    """Rozpozna zastupny nazev domeny u vlan-based instance.

    MX ji rika '__EVPN-VLAN-BASED-CPE13-NNI__', EVO 'VL-NONE'. Vlan-aware
    instance ma proti tomu skutecny nazev ('BD-313', 'VL-313').
    """
    upper = name.strip().upper()
    return upper.startswith("__") or upper.endswith("NONE")
