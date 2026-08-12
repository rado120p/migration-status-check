"""EVPN checky pro E-Line (vpws) a E-LAN (vlan-aware, vlan-based).

Platformni rozdil MX (virtual-switch/bridge-domain) vs EVO (mac-vrf/VLAN)
resi collector - sem uz prichazi jednotne schema, takze tady neni zadna
vetev na platformu.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from migration_validator.checks.base import Check, CheckContext, Mode
from migration_validator.checks.ifaces import percent_change, qualified
from migration_validator.checks.registry import register
from migration_validator.models.result import Finding, Outcome, Severity

UP = "Up"


def _is_up(status: str) -> bool:
    """Junos hlasi stav i s doplnkem za lomitkem.

    Lokalni rozhrani v ESI je 'Up/Forwarding', VPWS rozhrani jen 'Up'.
    Porovnani na presnou rovnost by to prvni oznacilo za rozbite.
    """
    return status.split("/", 1)[0].strip() == UP


def _esi_value(data: dict[str, Any] | None) -> str | None:
    if data is None:
        return None
    status = str(data.get("status", "unknown"))
    return f"{status}  DF {data.get('df_role') or '-'}"


def _find_baseline_peer(
    baseline_peers: list[dict[str, Any]], ipaddr: Any
) -> dict[str, Any] | None:
    """Najde baseline peera podle ipaddr v jiz pozicne sparovanem SID.

    Jmena rozhrani se migraci meni, ale IP adresa vzdaleneho PE ne - proto
    je ipaddr jedine spolehlive kriterium pro parovani peeru uvnitr SID.
    """
    for peer in baseline_peers:
        if peer.get("ipaddr") == ipaddr:
            return peer
    return None


@register
class EvpnVpwsStatusCheck(Check):
    """Stav EVPN-VPWS per SID a peer.

    Stav se ted cte vyhradne z 'evpn-vpws-sid-pe-status' - drivejsi
    porovnani jen SID cisel a stavu rozhrani tuhle tabulku vubec necetlo.
    Mode.BOTH je opravneny: baseline_value se dopocitava pozicnim
    parovanim rozhrani a peeru podle ipaddr (viz _find_baseline_peer),
    takze sloupec ZMENA nese skutecny rozdil, ne trvale "bez baseline".
    Drivejsi radek 'chybi remote SID' nahrazuji dva BROKEN radky remote
    PE / remote status.
    """

    id = "evpn_vpws_status"
    title = "Stav EVPN-VPWS"
    label = "EVPN VPWS status"
    mode = Mode.BOTH
    requires = ("evpn_vpws",)
    service_types = frozenset({"E-Line"})
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        instances: dict[str, Any] = ctx.subject.get("evpn_vpws", {})
        if not instances:
            return [
                Finding(Outcome.SKIP, "pro tento scope nejsou data evpn-vpws", value="bez dat")
            ]

        # Jmeno instance (RI) migraci prezije, takze parovani baseline
        # instance je proste podle jmena - na rozdil od rozhrani uvnitr ni.
        baseline_instances: dict[str, Any] = (ctx.baseline or {}).get("evpn_vpws", {})

        findings: list[Finding] = []
        for name in sorted(instances):
            interfaces = instances[name].get("interfaces", [])
            baseline_interfaces = baseline_instances.get(name, {}).get("interfaces", [])
            many = len(interfaces) > 1
            for idx, iface in enumerate(interfaces):
                # Pozicni parovani rozhrani: jmena rozhrani se migraci meni
                # (ge-0/0/3.0 -> ae0.224), takze jmeno pro parovani s
                # baseline pouzit nejde - stejny princip jako pozicni zip
                # v _aligned_baseline_data (engine.py).
                baseline_iface = (
                    baseline_interfaces[idx] if idx < len(baseline_interfaces) else None
                )
                findings.extend(
                    self._interface_findings(name, iface, baseline_iface, many)
                )
        return findings

    def _interface_findings(
        self,
        instance: str,
        iface: dict[str, Any],
        baseline_iface: dict[str, Any] | None,
        qualify: bool,
    ) -> list[Finding]:
        def label(text: str) -> str:
            return qualified(text, iface["name"]) if qualify else text

        findings = []
        status = str(iface.get("status", "unknown"))
        baseline_status = (
            str(baseline_iface.get("status", "unknown"))
            if baseline_iface is not None
            else None
        )
        findings.append(
            Finding(
                Outcome.OK if _is_up(status) else Outcome.BROKEN,
                f"{instance}: stav rozhrani {status}"
                + ("" if _is_up(status) else f", ocekavano {UP}"),
                label=label("EVPN VPWS local interface status"),
                value=status,
                baseline_value=baseline_status,
                subject={"interface": iface["name"], "status": status},
            )
        )
        findings.extend(self._sid_findings(instance, iface, baseline_iface, "local", label))
        findings.extend(self._sid_findings(instance, iface, baseline_iface, "remote", label))
        return findings

    def _sid_findings(
        self,
        instance: str,
        iface: dict[str, Any],
        baseline_iface: dict[str, Any] | None,
        side: str,
        label,
    ) -> list[Finding]:
        sid = iface.get(f"{side}_sid") or {"value": None, "peers": []}
        value = sid.get("value")
        peers = sid.get("peers") or []
        prefix = f"EVPN VPWS SID {side}"

        baseline_sid = (
            baseline_iface.get(f"{side}_sid") if baseline_iface is not None else None
        )
        baseline_peers = (baseline_sid.get("peers") or []) if baseline_sid else []
        baseline_sid_value = (
            f"SID {baseline_sid.get('value') if baseline_sid.get('value') is not None else '?'}"
            if baseline_sid is not None
            else None
        )

        findings = [
            Finding(
                Outcome.INFO,
                f"{instance}: {side} SID {value if value is not None else '?'}",
                label=label(f"{prefix} value"),
                value=f"SID {value if value is not None else '?'}",
                baseline_value=baseline_sid_value,
            )
        ]

        if not peers:
            if side == "remote":
                # Remote peer musi existovat vzdy - jeho absence znamena
                # nenakonfigurovanou nebo spadlou druhou stranu. Kdyz
                # baseline peer mela, "bylo Resolved" je presne informace,
                # kterou operator potrebuje - proto se pujcuje z prvniho
                # baseline peeru, i kdyz v subjektu zadny peer neni.
                first_baseline_peer = baseline_peers[0] if baseline_peers else None
                findings.append(
                    Finding(
                        Outcome.BROKEN,
                        f"{instance}: remote peer chybi",
                        label=label(f"{prefix} PE"),
                        value="Neznamy peer",
                        baseline_value=(
                            str(first_baseline_peer.get("ipaddr") or "?")
                            if first_baseline_peer
                            else None
                        ),
                    )
                )
                findings.append(
                    Finding(
                        Outcome.BROKEN,
                        f"{instance}: remote SID nema zadny Resolved zaznam",
                        label=label(f"{prefix} status"),
                        value="Unresolved / Chybi",
                        baseline_value=(
                            str(first_baseline_peer.get("status") or "Unresolved / Chybi")
                            if first_baseline_peer
                            else None
                        ),
                    )
                )
            else:
                # Local peery nese jen multihoming - u single-homed jde
                # o ocekavany stav, ne o vadu. Baseline lze pouzit jen
                # kdyz ma stejny tvar radku (taky bez local peeru) -
                # kdyz baseline peery MELA, jde o jiny tvar hlasky a
                # srovnani by nedavalo smysl, baseline_value zustava None.
                subject_mode = iface.get("mode") or "unknown"
                value = f"{subject_mode} (multi-homing peer ve vypisu nenalezen)"
                baseline_local_mode = None
                if (
                    baseline_iface is not None
                    and baseline_sid is not None
                    and not baseline_peers
                ):
                    baseline_mode = baseline_iface.get("mode") or "unknown"
                    # Pri shode modu jde do baseline_value cela hodnota
                    # radku, aby change_text poznal rovnost a nechal ZMENU
                    # prazdnou. Pri rozdilu jde jen cisty mod - zavorka o
                    # nenalezenem peeru popisuje subjekt, ne baseline, a
                    # "bylo single-homed (multi-homing peer...)" by tvrdila
                    # o baseline vic, nez check vi.
                    baseline_local_mode = (
                        value if baseline_mode == subject_mode else baseline_mode
                    )
                findings.append(
                    Finding(
                        Outcome.INFO,
                        f"{instance}: local strana bez multi-homing peeru",
                        label=label(f"{prefix} mode"),
                        value=value,
                        baseline_value=baseline_local_mode,
                    )
                )
            return findings

        peer_label = f"{prefix} peer PE" if side == "local" else f"{prefix} PE"
        for peer in peers:
            baseline_peer = _find_baseline_peer(baseline_peers, peer.get("ipaddr"))
            resolved = (peer.get("status") or "").strip().lower() == "resolved"
            outcome = Outcome.OK if resolved else Outcome.BROKEN
            findings.append(
                Finding(
                    outcome,
                    f"{instance}: {side} peer {peer.get('ipaddr')}",
                    label=label(peer_label),
                    value=str(peer.get("ipaddr") or "?"),
                    baseline_value=(
                        str(baseline_peer.get("ipaddr") or "?") if baseline_peer else None
                    ),
                    subject=dict(peer),
                )
            )
            findings.append(
                Finding(
                    outcome,
                    f"{instance}: {side} peer {peer.get('ipaddr')} "
                    f"status {peer.get('status') or 'chybi'}",
                    label=label(f"{prefix} status"),
                    value=str(peer.get("status") or "Unresolved / Chybi"),
                    baseline_value=(
                        str(baseline_peer.get("status") or "Unresolved / Chybi")
                        if baseline_peer
                        else None
                    ),
                )
            )
            for info_label, key in (("mode", "mode"), ("ESI", "esi"), ("role", "role")):
                if peer.get(key):
                    findings.append(
                        Finding(
                            Outcome.INFO,
                            f"{instance}: {side} peer {key} {peer[key]}",
                            label=label(f"{prefix} {info_label}"),
                            value=str(peer[key]),
                            baseline_value=(
                                str(baseline_peer[key])
                                if baseline_peer and baseline_peer.get(key)
                                else None
                            ),
                        )
                    )
        return findings


@register
class EvpnEsiStatusCheck(Check):
    id = "evpn_esi_status"
    title = "Stav EVPN ESI"
    label = "EVPN ESI status"
    mode = Mode.BOTH
    requires = ("evpn_esi",)
    service_types = frozenset({"E-LAN"})
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        entries: dict[str, Any] = ctx.subject.get("evpn_esi", {})
        if not entries:
            return [
                Finding(Outcome.SKIP, "pro tento scope nejsou data evpn-esi", value="bez dat")
            ]

        baseline_entries = (ctx.baseline or {}).get("evpn_esi", {})

        findings = []
        for esi in sorted(entries):
            data = entries[esi]
            status = str(data.get("status", "unknown"))
            subject = {
                "status": status,
                "df_role": data.get("df_role"),
                "interface": data.get("interface"),
            }
            baseline = baseline_entries.get(esi)
            outcome = Outcome.OK if _is_up(status) else Outcome.BROKEN
            message = (
                f"{esi}: {status}, DF {subject['df_role']}"
                if outcome is Outcome.OK
                else f"{esi}: stav rozhrani {status}, ocekavano {UP}"
            )
            findings.append(
                Finding(
                    outcome,
                    message,
                    label=esi,
                    value=_esi_value(subject),
                    baseline_value=_esi_value(baseline),
                    baseline=baseline,
                    subject=subject,
                )
            )
        return findings


def _count_finding(
    label: str,
    message: str,
    value: str,
    baseline_value: str | None,
    *,
    ok: bool,
    expectation: str,
    warn_below_baseline: bool = False,
) -> Finding:
    """Ciselny radek: stavove pravidlo; rozdil proti baseline nese ZMENA.

    Jedinym volajicim je EVPN neighbors (stavove pravidlo > 0). Rovnost s
    baseline se nevynucuje (revize spec 2.4 po overeni v laborce
    2026-08-06) - misto toho warn_below_baseline: pokles pod baseline je
    DEGRADED, ztraceny peer stoji za pozornost, ale u ciste L2 vlan-aware
    sluzby po migraci legitimne ubyde puvodni box, takze to neni tvrdy
    FAIL. Agregaty local/IRB interfacu, ktere driv rovnez pouzivaly tuhle
    funkci, Task 2 zrusil - jejich pocet se migraci meni pri kazde
    konsolidaci sluzeb do jedne mac-vrf instance a rovnost by FAILovala
    trvale, viz _ServiceUnits vyse.
    """
    outcome = Outcome.OK if ok else Outcome.BROKEN
    text = f"{message}: {value}" if ok else f"{message}: {value}, ocekavano {expectation}"
    if (
        ok
        and warn_below_baseline
        and baseline_value is not None
        and int(value) < int(baseline_value)
    ):
        outcome = Outcome.DEGRADED
        text = f"{message}: {value}, baseline {baseline_value}"
    return Finding(
        outcome, text, label=label, value=value, baseline_value=baseline_value
    )


@dataclass(frozen=True)
class _ServiceUnits:
    """Identita sluzby pro relevance filtr vlan-aware bloku.

    active=False vypina filtrovani - scope bez interface selektoru nema
    podle ceho vybirat a radsi vypise vsechno nez nic.
    """

    interfaces: frozenset[str]
    irb: str | None
    vlans: frozenset[str]

    @property
    def active(self) -> bool:
        return bool(self.interfaces)


def _service_units(ctx: CheckContext) -> _ServiceUnits:
    interfaces = frozenset(ctx.scope.selectors.interfaces)
    irb = None
    if ctx.link and ctx.link.get("role") == "l2":
        irb = ctx.link.get("peer_interface")
    vlans = frozenset(ctx.scope.selectors.vlans)
    if not vlans:
        # sluzba bez customer_vlan - unit cislo je stejna informace
        vlans = frozenset(
            name.split(".", 1)[1] for name in interfaces if "." in name
        )
    return _ServiceUnits(interfaces=interfaces, irb=irb, vlans=vlans)


_IFL_RE = re.compile(r"by IFL (\S+)")


def _esi_is_relevant(status: str | None, units: _ServiceUnits) -> bool:
    """ESI patri sluzbe, kdyz jeho status jmenuje jeji IFL.

    Unresolved status IFL nenese, takze pri aktivnim filtru vypadne -
    vlastni ESI sluzby posuzuje DF radek evpn_esi_status (interface-
    filtrovany), tenhle listing je jen instancni kontext.
    """
    if not status:
        return False
    match = _IFL_RE.search(status)
    return bool(match) and match.group(1) in units.interfaces


@register
class EvpnInstanceStatusCheck(Check):
    """Per-instance zdravi EVPN podle brief pravidel ze zadani.

    RI-wide agregaty local/IRB interfacu (Task 2, spec 2026-08-12) jsou
    zrusene - pocet interfacu v instanci se migraci konsolidace sluzeb do
    jedne mac-vrf meni pri kazde migraci a byl by trvale nepouzitelny pro
    porovnani s baseline. Misto nich se posuzuji jen radky vlastnich unitu
    sluzby: "EVPN interface" (filtr na ctx.scope.selectors.interfaces) a
    "IRB interface" (jen kdyz je unit linkovany pres ctx.link, role "l2") -
    viz _service_units. Kdyz vlastni (nebo linkovany IRB) unit v instanci
    vubec neni - IFL se do mac-vrf nedostal, realny selhany stav migrace -
    misto ticha se emituje BROKEN radek "{unit} chybi v instanci" (dodatek
    specu 2026-08-12, review Tasku 2); u vice instanci ve scope (qualify)
    se tenhle radek preskakuje, protoze bez vedeni "ktera instance je ta
    spravna" by naivni per-instance kontrola falesne broken-ovala unit v
    kazde jine instanci. Realne service scopy (scoping/builder.py) maji
    vzdy presne jednu routing_instance, takze qualify=True u service
    scopu dnes nenastava - pojistka je precautionary, ne znama mezera.
    EVPN neighbors pod baseline jsou DEGRADED (viz _count_finding). Text
    ESI statusu se na rovnost neporovnava, nese jmeno IFL.
    """

    id = "evpn_instance_status"
    title = "Stav EVPN instance"
    label = "EVPN instance"
    mode = Mode.BOTH
    requires = ("evpn_instance",)
    service_types = frozenset({"E-LAN"})
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        instances: dict[str, Any] = ctx.subject.get("evpn_instance", {})
        if not instances:
            return [
                Finding(
                    Outcome.SKIP,
                    "pro tento scope nejsou data evpn-instance",
                    value="bez dat",
                )
            ]

        baseline_instances = (ctx.baseline or {}).get("evpn_instance", {})
        many = len(instances) > 1
        units = _service_units(ctx)

        findings: list[Finding] = []
        for name in sorted(instances):
            findings.extend(
                self._instance_findings(
                    name, instances[name], baseline_instances.get(name), many, units
                )
            )
        return findings

    def _instance_findings(
        self,
        instance: str,
        data: dict[str, Any],
        baseline: dict[str, Any] | None,
        qualify: bool,
        units: _ServiceUnits,
    ) -> list[Finding]:
        def label(text: str) -> str:
            return qualified(text, instance) if qualify else text

        baseline = baseline or {}
        findings: list[Finding] = []

        neighbors = data.get("neighbors", {})
        baseline_neighbors = baseline.get("neighbors") or None
        findings.append(
            _count_finding(
                label("EVPN neighbors"),
                f"{instance}: EVPN neighbors",
                str(neighbors.get("total") or 0),
                (
                    str(baseline_neighbors["total"])
                    if baseline_neighbors
                    else None
                ),
                ok=(neighbors.get("total") or 0) > 0,
                expectation="> 0",
                warn_below_baseline=True,
            )
        )

        for address in neighbors.get("addresses", []):
            findings.append(
                Finding(
                    Outcome.INFO,
                    f"{instance}: neighbor {address}",
                    label=label("EVPN neighbor"),
                    value=address,
                )
            )

        findings.extend(self._esi_findings(instance, data, baseline, label, units))

        local = data.get("local_interfaces", {})
        local_entries = local.get("entries", [])
        local_names = {entry["name"] for entry in local_entries}
        for entry in local_entries:
            if units.active and entry["name"] not in units.interfaces:
                continue
            up = _is_up(str(entry["status"]))
            findings.append(
                Finding(
                    Outcome.OK if up else Outcome.BROKEN,
                    f"{instance}: interface {entry['name']} {entry['status']}"
                    + ("" if up else f", ocekavano {UP}"),
                    label=label("EVPN interface"),
                    value=f"{entry['name']} {entry['status']}",
                )
            )
        # IFL, ktery se do mac-vrf teto instance vubec nedostal, by jinak
        # zustal neviditelny - blok by pro sluzbu nevypsal zadny EVPN
        # interface radek (dodatek specu 2026-08-12, review Tasku 2).
        # Multi-instance scope: unit patri tomu RI, ktere ho jmenuje, takze
        # se pri vice instancich (qualify=True) chybejici-unit radek
        # neemituje - jinak by naivni per-instance kontrola falesne
        # nahlasila unit jako chybejici v kazde instanci krome te spravne.
        if units.active and not qualify:
            for unit in sorted(units.interfaces - local_names):
                findings.append(
                    Finding(
                        Outcome.BROKEN,
                        f"{instance}: unit {unit} chybi v instanci",
                        label=label("EVPN interface"),
                        value=f"{unit} chybi v instanci",
                    )
                )

        irb = data.get("irb_interfaces", {})
        irb_entries = irb.get("entries", [])
        irb_names = {entry["name"] for entry in irb_entries}
        for entry in irb_entries:
            if units.active and entry["name"] != units.irb:
                continue
            context = entry.get("l3_context")
            value = f"{entry['name']} {entry['status']}"
            if context:
                value += f" ({context})"
            up = _is_up(str(entry["status"]))
            findings.append(
                Finding(
                    Outcome.OK if up else Outcome.BROKEN,
                    f"{instance}: IRB {value}"
                    + ("" if up else f", ocekavano {UP}"),
                    label=label("IRB interface"),
                    value=value,
                )
            )
        if units.active and not qualify and units.irb and units.irb not in irb_names:
            findings.append(
                Finding(
                    Outcome.BROKEN,
                    f"{instance}: IRB unit {units.irb} chybi v instanci",
                    label=label("IRB interface"),
                    value=f"{units.irb} chybi v instanci",
                )
            )
        return findings

    def _esi_findings(
        self,
        instance: str,
        data: dict[str, Any],
        baseline: dict[str, Any],
        label,
        units: _ServiceUnits,
    ) -> list[Finding]:
        esis: dict[str, str] = data.get("esis", {})
        baseline_esis: dict[str, str] = baseline.get("esis", {}) if baseline else {}

        if not esis and not baseline_esis:
            # Single-homed instance zadne konfigurovane ESI nema - bez dat
            # je vysledek SKIP, ne chyba (spec 2.2).
            return [
                Finding(
                    Outcome.SKIP,
                    f"{instance}: zadne ESI ve vypisu",
                    label=label("ESI status"),
                    value="bez dat",
                )
            ]

        findings = []
        for esi in sorted(set(esis) | set(baseline_esis)):
            status = esis.get(esi)
            baseline_status = baseline_esis.get(esi)
            if units.active and not _esi_is_relevant(status, units):
                # Vetev "status is None" (baseline melo, ted chybi) tady
                # vypada spolu s ostatnimi - baseline IFL nese stare jmeno
                # rozhrani, proti novym selektorum nikdy nesedi (zamer,
                # viz spec kap. 2). Zdravi vlastniho ESI nese DF radek
                # checku evpn_esi_status (interface-filtrovany).
                continue
            if status is None:
                findings.append(
                    Finding(
                        Outcome.BROKEN,
                        f"{instance}: ESI {esi} v baseline bylo, ted chybi",
                        label=label(f"ESI {esi}"),
                        value="chybi",
                        baseline_value=baseline_status,
                    )
                )
                continue
            # Substring by chytl i "Unresolved" - stejny duvod, proc VPWS
            # check (radek vyse) porovnava cele slovo, ne podretezec.
            resolved = status.lower().startswith("resolved")
            findings.append(
                Finding(
                    Outcome.OK if resolved else Outcome.BROKEN,
                    f"{instance}: ESI {esi} {status or 'bez statusu'}",
                    label=label(f"ESI {esi}"),
                    value=status or "bez statusu",
                    # Text nese jmeno IFL, ktere se migraci meni - baseline
                    # se ukazuje, ale na rovnost se neporovnava.
                    baseline_value=baseline_status,
                )
            )
        return findings


@register
class EvpnMacCountCheck(Check):
    id = "evpn_mac_count"
    title = "Pocet MAC adres"
    label = "EVPN MAC count"
    mode = Mode.BOTH
    requires = ("evpn_mac",)
    service_types = frozenset({"E-LAN"})
    default_severity = Severity.ADVISORY

    def run(self, ctx: CheckContext) -> list[Finding]:
        instances: dict[str, Any] = ctx.subject.get("evpn_mac", {})
        if not instances:
            return [
                Finding(
                    Outcome.SKIP, "pro tento scope nejsou data o MAC adresach", value="bez dat"
                )
            ]

        baseline_instances = (ctx.baseline or {}).get("evpn_mac", {})
        tolerance = float(ctx.options(self.id)["tolerance_percent"])
        many = len(instances) > 1

        findings = []
        for instance in sorted(instances):
            data = instances[instance]
            baseline = baseline_instances.get(instance, {})

            def label(text: str) -> str:
                return qualified(text, instance) if many else text

            subject_vlans = data.get("vlans", {})
            baseline_vlans = baseline.get("vlans", {})
            # Union se baseline: count vypis mrtvou domenu vubec neuvadi,
            # iterace jen pres subject by jeji zmizeni tise zahodila.
            for vlan in sorted(
                set(subject_vlans) | set(baseline_vlans),
                key=lambda v: int(v) if v.isdigit() else 0,
            ):
                subject_entry = subject_vlans.get(vlan)
                baseline_entry = baseline_vlans.get(vlan)
                domain = (subject_entry or baseline_entry).get("domain")
                row_label = label(f"{domain} MAC count" if domain else "MAC count")
                if subject_entry is None:
                    findings.append(
                        _mac_compare_finding(
                            row_label, int(baseline_entry["count"]), 0, tolerance
                        )
                    )
                elif baseline_entry is None:
                    findings.append(
                        _mac_state_finding(row_label, int(subject_entry["count"]))
                    )
                else:
                    findings.append(
                        _mac_compare_finding(
                            row_label,
                            int(baseline_entry["count"]),
                            int(subject_entry["count"]),
                            tolerance,
                        )
                    )

            baseline_interfaces = baseline.get("interfaces", {})
            # Per-interface se iteruje jen subject: kdyz box interface-name
            # nevrati (EVO count vypis), radek se vynechava - rozhodnuti
            # ze specu, per-VLAN uroven je vzdy pokryta.
            for key in sorted(data.get("interfaces", {})):
                entry = data["interfaces"][key]
                domain = entry.get("domain")
                prefix = f"{domain} " if domain else ""
                row_label = label(f"{prefix}Interface {entry['name']} MAC count")
                baseline_entry = baseline_interfaces.get(key)
                if baseline_entry is None:
                    findings.append(
                        _mac_state_finding(row_label, int(entry["count"]))
                    )
                else:
                    findings.append(
                        _mac_compare_finding(
                            row_label,
                            int(baseline_entry["count"]),
                            int(entry["count"]),
                            tolerance,
                        )
                    )
        return findings


def _mac_state_finding(label: str, count: int) -> Finding:
    if count == 0:
        return Finding(
            Outcome.BROKEN,
            f"{label}: 0 naucenych MAC adres",
            label=label,
            value="0",
            subject={"mac_count": 0},
        )
    return Finding(
        Outcome.OK,
        f"{label}: {count} naucenych MAC adres",
        label=label,
        value=str(count),
        subject={"mac_count": count},
    )


def _mac_compare_finding(
    label: str, baseline: int, subject: int, tolerance: float
) -> Finding:
    change = percent_change(baseline, subject)
    details: dict[str, Any] = {"tolerance_percent": tolerance}
    if change is not None:
        details["change_percent"] = round(change, 1)

    # Stejna trojice poli jako u BGP counteru (AR-4): hodnota, drivejsi
    # hodnota a hotovy rozdil. Bez baseline_value zustal sloupec ZMENA
    # u MAC prazdny, prestoze check obe cisla znal.
    presentation = {
        "value": str(subject),
        "baseline_value": str(baseline),
        "delta": f"{subject - baseline:+d}" if subject != baseline else None,
    }

    if change is not None and change < tolerance:
        return Finding(
            Outcome.BROKEN,
            f"{label}: pocet MAC klesl {baseline} -> {subject}, "
            f"prah je {tolerance:.0f} %",
            label=label,
            baseline={"mac_count": baseline},
            subject={"mac_count": subject},
            details=details,
            **presentation,
        )
    if subject == 0:
        return Finding(
            Outcome.BROKEN,
            f"{label}: 0 naucenych MAC adres (baseline {baseline})",
            label=label,
            baseline={"mac_count": baseline},
            subject={"mac_count": 0},
            details=details,
            **presentation,
        )
    return Finding(
        Outcome.OK,
        f"{label}: {subject} MAC adres, v toleranci {tolerance:.0f} %",
        label=label,
        baseline={"mac_count": baseline},
        subject={"mac_count": subject},
        details=details,
        **presentation,
    )
