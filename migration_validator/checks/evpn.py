"""EVPN checky pro E-Line (vpws) a E-LAN (vlan-aware, vlan-based).

Platformni rozdil MX (virtual-switch/bridge-domain) vs EVO (mac-vrf/VLAN)
resi collector - sem uz prichazi jednotne schema, takze tady neni zadna
vetev na platformu.
"""

from __future__ import annotations

from typing import Any

from migration_validator.checks.base import Check, CheckContext, Mode
from migration_validator.checks.ifaces import percent_change
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


@register
class EvpnVpwsStatusCheck(Check):
    id = "evpn_vpws_status"
    title = "Stav EVPN-VPWS"
    mode = Mode.BOTH
    requires = ("evpn_vpws",)
    service_types = frozenset({"E-Line"})
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        instances: dict[str, Any] = ctx.subject.get("evpn_vpws", {})
        if not instances:
            return [Finding(Outcome.SKIP, "pro tento scope nejsou data evpn-vpws")]

        baseline_instances = (ctx.baseline or {}).get("evpn_vpws", {})

        findings = []
        for name in sorted(instances):
            data = instances[name]
            status = str(data.get("status", "unknown"))
            local = data.get("local_sid")
            remote = data.get("remote_sid")
            subject = {"status": status, "local_sid": local, "remote_sid": remote}
            baseline = baseline_instances.get(name)

            if not _is_up(status):
                findings.append(
                    Finding(
                        Outcome.BROKEN,
                        f"{name}: stav rozhrani {status}, ocekavano {UP}",
                        label=name,
                        baseline=baseline,
                        subject=subject,
                    )
                )
                continue

            # local a remote SID se u EVPN-VPWS zamerne lisi - kazda strana
            # inzeruje svoje service ID ('local 1000; remote 2000'). Rovnost
            # tady neni invariant, chybi az kdyz remote SID vubec neprijde.
            if not remote:
                findings.append(
                    Finding(
                        Outcome.BROKEN,
                        f"{name}: chybi remote SID (local {local})",
                        label=name,
                        baseline=baseline,
                        subject=subject,
                    )
                )
                continue

            findings.append(
                Finding(
                    Outcome.OK,
                    f"{name}: {UP}, SID {local} -> {remote}",
                    label=name,
                    baseline=baseline,
                    subject=subject,
                )
            )
        return findings


@register
class EvpnEsiStatusCheck(Check):
    id = "evpn_esi_status"
    title = "Stav EVPN ESI"
    mode = Mode.BOTH
    requires = ("evpn_esi",)
    service_types = frozenset({"E-LAN"})
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        entries: dict[str, Any] = ctx.subject.get("evpn_esi", {})
        if not entries:
            return [Finding(Outcome.SKIP, "pro tento scope nejsou data evpn-esi")]

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
                Finding(outcome, message, label=esi, baseline=baseline, subject=subject)
            )
        return findings


@register
class EvpnMacCountCheck(Check):
    id = "evpn_mac_count"
    title = "Pocet MAC adres"
    mode = Mode.BOTH
    requires = ("evpn_mac",)
    service_types = frozenset({"E-LAN"})
    default_severity = Severity.ADVISORY

    def run(self, ctx: CheckContext) -> list[Finding]:
        instances: dict[str, Any] = ctx.subject.get("evpn_mac", {})
        if not instances:
            return [Finding(Outcome.SKIP, "pro tento scope nejsou data o MAC adresach")]

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
            subject={"mac_count": 0},
        )
    return Finding(
        Outcome.OK,
        f"{label}: {count} naucenych MAC adres",
        label=label,
        subject={"mac_count": count},
    )


def _mac_compare_finding(
    label: str, baseline: int, subject: int, tolerance: float
) -> Finding:
    change = percent_change(baseline, subject)
    details: dict[str, Any] = {"tolerance_percent": tolerance}
    if change is not None:
        details["change_percent"] = round(change, 1)

    if change is not None and change < tolerance:
        return Finding(
            Outcome.BROKEN,
            f"{label}: pocet MAC klesl {baseline} -> {subject}, "
            f"prah je {tolerance:.0f} %",
            label=label,
            baseline={"mac_count": baseline},
            subject={"mac_count": subject},
            details=details,
        )
    if subject == 0:
        return Finding(
            Outcome.BROKEN,
            f"{label}: 0 naucenych MAC adres (baseline {baseline})",
            label=label,
            baseline={"mac_count": baseline},
            subject={"mac_count": 0},
            details=details,
        )
    return Finding(
        Outcome.OK,
        f"{label}: {subject} MAC adres, v toleranci {tolerance:.0f} %",
        label=label,
        baseline={"mac_count": baseline},
        subject={"mac_count": subject},
        details=details,
    )
