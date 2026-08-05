"""EVPN checky pro E-Line (vpws) a E-LAN (vlan-aware, vlan-based).

Platformni rozdil MX (virtual-switch/bridge-domain) vs EVO (mac-vrf/VLAN)
resi collector - sem uz prichazi jednotne schema, takze tady neni zadna
vetev na platformu.
"""

from __future__ import annotations

from typing import Any

from migration_validator.checks.base import Check, CheckContext, Mode
from migration_validator.checks.ifaces import percent_change, qualified
from migration_validator.checks.registry import register
from migration_validator.models.result import Finding, Outcome, Severity

UP = "Up"
NO_DOMAIN = "-"


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

        findings = []
        for instance in sorted(instances):
            domains = instances[instance]
            for domain in sorted(domains):
                count = int(domains[domain])
                label = instance if domain == NO_DOMAIN else f"{instance}/{domain}"
                baseline_count = (
                    baseline_instances.get(instance, {}).get(domain)
                    if instance in baseline_instances
                    else None
                )

                if baseline_count is None:
                    findings.append(_mac_state_finding(label, count))
                    continue

                findings.append(
                    _mac_compare_finding(label, int(baseline_count), count, tolerance)
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
