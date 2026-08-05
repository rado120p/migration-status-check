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

from collections import defaultdict
from typing import Any

from lxml import etree

from migration_validator.collectors.base import Collector, CollectorError
from migration_validator.collectors.interfaces import _int, _text
from migration_validator.collectors.registry import register

NO_DOMAIN = "-"


@register
class EvpnVpwsCollector(Collector):
    """Stav EVPN-VPWS instanci.

    local_sid a remote_sid se ctou z prvniho rozhrani instance v poradi
    dokumentu. Instance s vice rozhranimi je v teto topologii vzacna a
    kontrakt typuje SID jako jedno cislo; kdyby bylo potreba pokryt vic
    rozhrani, musi se nejdriv rozsirit schema ve specu.
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

            interface = node.find(
                "evpn-vpws-interface-status-table/evpn-vpws-interface"
            )

            instances[name] = {
                "local_sid": _int(
                    interface,
                    "evpn-vpws-service-id-local-status-table/evpn-vpws-sid-local/"
                    "evpn-vpws-sid-local-value",
                ),
                "remote_sid": _int(
                    interface,
                    "evpn-vpws-service-id-remote-status-table/evpn-vpws-sid-remote/"
                    "evpn-vpws-sid-remote-value",
                ),
                "status": self._status(interface),
            }

        return instances

    @staticmethod
    def _status(interface: etree._Element | None) -> str:
        """Stav rozhrani instance ('Up'), ne stav vzdaleneho PE ('Resolved').

        Obe hodnoty existuji a znamenaji neco jineho. Check evpn_vpws_status
        porovnava proti 'Up', takze emitujeme tu, ktera je s nim souměřitelná.
        """
        if interface is None:
            return "unknown"
        return _text(interface, "evpn-vpws-interface-status") or "unknown"


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
class EvpnMacCollector(Collector):
    """Pocty naucenych MAC adres na instanci a bridge domenu."""

    name = "evpn_mac"

    # Poradi je zamerne: prvni je "hlavni" RPC, ktere pouzije `record`
    # a `--record-raw` pri ukladani fixtures.
    #
    # MX potrebuje dve RPC, protoze kazde vidi jiny typ instance. EVO ma
    # jen jedno - 'show evpn mac-table' na PTX vubec neexistuje, zatimco
    # mac-vrf tabulka tam vraci vlan-aware i vlan-based instance zaraz.
    # Proto se tady uvadeji jen RPC, ktera na dane platforme opravdu plati:
    # selhani kteregokoliv z nich pak znamena skutecnou chybu, ne to, ze
    # jsme se zeptali na neco, co ta platforma nezna.
    RPCS: dict[str, tuple[str, ...]] = {
        "junos": ("get_bridge_mac_table", "get_evpn_mac_table"),
        "junos-evo": ("get_mac_vrf_mac_table",),
    }

    def rpc_name(self, platform: str) -> str:
        return self.RPCS[platform][0]

    def rpc_names(self, platform: str) -> tuple[str, ...]:
        return self.RPCS[platform]

    def collect(self, device: Any, platform: str) -> dict[str, dict[str, int]]:
        """Slouci vysledky vsech RPC pro danou platformu.

        Kdyz nektere RPC selze, je to chyba celeho collectoru. Vratit
        castecna data jako 'ok' by znamenalo, ze check porovna zkraceny
        pocet MAC adres proti plnemu baseline a vyhodnoti to jako propad.
        Radsi SKIP nez tichy nesmysl.
        """
        if not self.supports(platform):
            raise CollectorError(
                f"collector '{self.name}' nepodporuje platformu '{platform}'"
            )

        merged: dict[str, dict[str, int]] = {}
        failures: list[str] = []

        for rpc_name in self.rpc_names(platform):
            try:
                xml = getattr(device.rpc, rpc_name)()
            except Exception as error:  # noqa: BLE001
                failures.append(f"{rpc_name}: {type(error).__name__}: {error}")
                continue

            try:
                parsed = self.parse(xml, platform)
            except Exception as error:  # noqa: BLE001
                failures.append(f"{rpc_name}: parsovani selhalo - {error}")
                continue

            for instance, domains in parsed.items():
                target = merged.setdefault(instance, {})
                for domain, count in domains.items():
                    target[domain] = target.get(domain, 0) + count

        if failures:
            raise CollectorError(
                f"collector '{self.name}': RPC selhalo - " + "; ".join(failures)
            )

        return merged

    # Tvary MAC tabulky. MX pouziva l2ald-*, EVO l2ng-l2ald-*. Prochazi se
    # oba, takze parse() nepotrebuje vetev na platformu ani spravny platform
    # argument - staci mu XML, coz drzi testy jednoduche.
    #
    # (skupina, routing-instance, vlan-id, nazev domeny, zaznam MAC)
    SHAPES: tuple[tuple[str, str, str, str, str], ...] = (
        (
            "l2ald-mac-entry",
            "l2-mac-routing-instance",
            "l2-bridge-vlan",
            "l2-mac-bridging-domain",
            "l2-mac-entry",
        ),
        (
            "l2ng-l2ald-mac-entry-vlan",
            "l2ng-l2-mac-routing-instance",
            "l2ng-l2-vlan-id",
            "l2ng-mac-entry/l2ng-l2-mac-vlan-name",
            "l2ng-mac-entry",
        ),
    )

    def parse(self, xml: etree._Element, platform: str) -> dict[str, dict[str, int]]:
        counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

        for group_tag, instance_tag, vlan_tag, name_tag, entry_tag in self.SHAPES:
            for group in xml.iter(group_tag):
                instance = _text(group, instance_tag)
                if not instance:
                    continue

                domain = _normalise_domain(
                    _text(group, vlan_tag), _text(group, name_tag)
                )
                counts[instance][domain] += len(group.findall(entry_tag))

        return {instance: dict(domains) for instance, domains in counts.items()}


def _is_no_domain(name: str) -> bool:
    """Rozpozna zastupny nazev domeny u vlan-based instance.

    MX ji rika '__EVPN-VLAN-BASED-CPE13-NNI__', EVO 'VL-NONE'. Vlan-aware
    instance ma proti tomu skutecny nazev ('BD-313', 'VL-313').
    """
    upper = name.strip().upper()
    return upper.startswith("__") or upper.endswith("NONE")


def _normalise_domain(vlan: str | None, name: str | None) -> str:
    """Klicem domeny je VLAN id, ne jeji nazev.

    Nazev se pro tu samou domenu mezi platformami lisi - MX ji rika 'BD-313',
    EVO 'VL-313'. Kdyby se klicovalo nazvem, check evpn_mac_count by po
    migraci nenasel domenu v baseline a misto porovnani poctu MAC adres by
    vypsal jen stav. VLAN id (313) je na obou stranach totozne.

    Vlan-based instance zadnou vlastni domenu nema a kontrakt pro ni
    predepisuje '-'. MX to prozradi tim, ze vlan hlasi jako 'none', EVO
    tim, ze domene rika 'VL-NONE' - VLAN id ale uvede, takze podle nej
    samotneho by vlan-based instance na obou stranach nesedely.
    """
    if name is not None and _is_no_domain(name):
        return NO_DOMAIN
    if vlan is None:
        return NO_DOMAIN
    vlan = vlan.strip()
    return vlan if vlan.isdigit() else NO_DOMAIN
