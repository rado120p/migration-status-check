"""ARP a ping checky.

Oba jsou vedome best-effort - CPE muze byt vypnute nebo blokovat ICMP -
proto default severity advisory. Cile pingu se resolvuji uz pri capture
(ARP -> ping), tady se ctou hotove vysledky ze snapshotu.
"""

from __future__ import annotations

from typing import Any

from migration_validator.checks.base import Check, CheckContext, Mode
from migration_validator.checks.registry import register
from migration_validator.models.result import Finding, Outcome, Severity

CUSTOMER_SERVICE_TYPES = frozenset({"Internet", "IPVPN"})


@register
class ArpPresentCheck(Check):
    id = "arp_present"
    title = "Existence ARP zaznamu"
    mode = Mode.STATE
    requires = ("arp",)
    requires_inventory = True
    service_types = CUSTOMER_SERVICE_TYPES
    default_severity = Severity.ADVISORY

    def run(self, ctx: CheckContext) -> list[Finding]:
        entries: list[dict[str, Any]] = ctx.subject.get("arp", [])
        addresses = [str(entry.get("ip")) for entry in entries if entry.get("ip")]

        if not addresses:
            return [
                Finding(
                    Outcome.BROKEN,
                    "na rozhranich sluzby neni zadny ARP zaznam",
                    subject={"count": 0, "addresses": []},
                )
            ]

        return [
            Finding(
                Outcome.OK,
                f"nalezeno {len(addresses)} ARP zaznamu: {', '.join(addresses)}",
                subject={"count": len(addresses), "addresses": addresses},
            )
        ]


@register
class PingReachabilityCheck(Check):
    id = "ping_reachability"
    title = "Dosazitelnost CPE pingem"
    mode = Mode.STATE
    requires = ("ping",)
    requires_inventory = True
    service_types = CUSTOMER_SERVICE_TYPES
    default_severity = Severity.ADVISORY

    def run(self, ctx: CheckContext) -> list[Finding]:
        probes: list[dict[str, Any]] = ctx.subject.get("ping", [])
        if not probes:
            return [
                Finding(
                    Outcome.SKIP,
                    "pro tento scope nejsou ve snapshotu zadne cile pingu",
                )
            ]

        targets: dict[str, dict[str, Any]] = {}
        unreachable: list[str] = []
        for probe in probes:
            target = str(probe.get("target"))
            received = int(probe.get("received", 0))
            targets[target] = {
                "sent": int(probe.get("sent", 0)),
                "received": received,
                "loss_percent": probe.get("loss_percent"),
                "resolved_from": probe.get("resolved_from"),
                "rtt_avg_ms": probe.get("rtt_avg_ms"),
            }
            if received == 0:
                unreachable.append(target)

        total = len(targets)
        reachable = total - len(unreachable)
        details = {"total": total, "reachable": reachable, "targets": targets}

        if reachable == total:
            return [
                Finding(
                    Outcome.OK,
                    f"vsech {total} cilu odpovedelo",
                    subject={"reachable": reachable, "total": total},
                    details=details,
                )
            ]

        if reachable == 0:
            return [
                Finding(
                    Outcome.BROKEN,
                    f"zadny z {total} cilu neodpovedel ({', '.join(unreachable)})",
                    subject={"reachable": 0, "total": total},
                    details=details,
                )
            ]

        return [
            Finding(
                Outcome.DEGRADED,
                f"odpovedelo {reachable} z {total} cilu, "
                f"neodpovedelo: {', '.join(unreachable)}",
                subject={"reachable": reachable, "total": total},
                details=details,
            )
        ]
