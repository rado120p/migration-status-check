"""Checky nad rozhranimi.

Counter-based checky bezi jen na tranzitnich rozhranich. Na internich davaji
SKIP, ne WARN - SKIP znamena "tenhle test sem nepatri", WARN "neco je spatne".
Kdyby interni rozhrani trvale svitila oranzove, operator si zvykne vystup
preskakovat.
"""

from __future__ import annotations

from typing import Any

from migration_validator.checks.base import Check, CheckContext, Mode
from migration_validator.checks.registry import register
from migration_validator.models.result import Finding, Outcome, Severity

TRANSIT_PREFIXES = ("ge", "xe", "et", "ae")

INTERNAL_PREFIXES = (
    "fxp", "em", "me", "bme", "cbp", "pip", "tap", "jsrv", "esi", "vtep", "pp0",
    "lc-", "demux", "lsi", "mtun", "pime", "pimd", "gre", "ipip", "dsc", "pfe",
    "pfh", "vcp", "sxe", "vme", "fti", "lo0", "re0", "irb",
)


def is_transit(interface: str) -> bool:
    """Nese rozhrani zakaznicky provoz? Allowlist ge/xe/et/ae."""
    physical = interface.split(".", 1)[0]
    return physical.startswith(TRANSIT_PREFIXES)


def percent_change(old: float, new: float) -> float | None:
    """Zmena v procentech. None kdyz baseline byla nulova (delit nulou nelze)."""
    if not old:
        return None
    return (new - old) / old * 100.0


def _transit_interfaces(ctx: CheckContext) -> list[str]:
    return sorted(name for name in ctx.subject.get("interfaces", {}) if is_transit(name))


def _no_transit_finding(ctx: CheckContext) -> Finding:
    names = sorted(ctx.subject.get("interfaces", {}))
    listed = ", ".join(names) if names else "zadne rozhrani"
    return Finding(
        outcome=Outcome.SKIP,
        message=f"neni tranzitni rozhrani ({listed}), counter check se preskakuje",
    )


@register
class InterfaceStateCheck(Check):
    id = "interface_state"
    title = "Stav rozhrani"
    mode = Mode.STATE
    requires = ("interfaces",)
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        interfaces: dict[str, Any] = ctx.subject.get("interfaces", {})
        if not interfaces:
            return [Finding(Outcome.SKIP, "pro tento scope nejsou data o rozhranich")]

        findings = []
        for name in sorted(interfaces):
            data = interfaces[name]
            admin = str(data.get("admin_status", "unknown"))
            oper = str(data.get("oper_status", "unknown"))
            state = {"admin_status": admin, "oper_status": oper}
            if admin == "up" and oper == "up":
                findings.append(
                    Finding(Outcome.OK, f"{name}: up/up", label=name, subject=state)
                )
            else:
                findings.append(
                    Finding(
                        Outcome.BROKEN,
                        f"{name}: admin {admin}, oper {oper}",
                        label=name,
                        subject=state,
                    )
                )
        return findings


@register
class InterfaceErrorsCheck(Check):
    id = "interface_errors"
    title = "Chybove countery rozhrani"
    mode = Mode.STATE
    requires = ("interfaces",)
    default_severity = Severity.ADVISORY

    def run(self, ctx: CheckContext) -> list[Finding]:
        names = _transit_interfaces(ctx)
        if not names:
            return [_no_transit_finding(ctx)]

        findings = []
        for name in names:
            data = ctx.subject["interfaces"][name]
            counters = {
                key: int(data.get(key, 0))
                for key in ("input_errors", "output_errors", "framing_errors")
                if key in data
            }
            total = sum(counters.values())
            if total == 0:
                findings.append(
                    Finding(Outcome.OK, f"{name}: bez chyb", label=name, subject=counters)
                )
            else:
                detail = ", ".join(f"{key}={value}" for key, value in counters.items() if value)
                findings.append(
                    Finding(
                        Outcome.BROKEN,
                        f"{name}: chybove countery nenulove ({detail})",
                        label=name,
                        subject=counters,
                    )
                )
        return findings


@register
class InterfaceTrafficCheck(Check):
    id = "interface_traffic"
    title = "Datovost rozhrani"
    mode = Mode.BOTH
    requires = ("interfaces",)
    default_severity = Severity.ADVISORY

    def run(self, ctx: CheckContext) -> list[Finding]:
        names = _transit_interfaces(ctx)
        if not names:
            return [_no_transit_finding(ctx)]

        options = ctx.options(self.id)
        tolerance = float(options["tolerance_percent"])
        require_nonzero = bool(options["require_nonzero"])

        findings = []
        for name in names:
            subject = _rates(ctx.subject["interfaces"][name])
            baseline_data = (ctx.baseline or {}).get("interfaces", {}).get(name)

            if baseline_data is None:
                findings.append(_state_finding(name, subject, require_nonzero))
                continue

            baseline = _rates(baseline_data)
            findings.append(_compare_finding(name, baseline, subject, tolerance))
        return findings


def _rates(data: dict[str, Any]) -> dict[str, int]:
    return {
        "input_pps": int(data.get("input_pps", 0)),
        "output_pps": int(data.get("output_pps", 0)),
    }


def _state_finding(name: str, subject: dict[str, int], require_nonzero: bool) -> Finding:
    if require_nonzero and (subject["input_pps"] == 0 or subject["output_pps"] == 0):
        return Finding(
            Outcome.BROKEN,
            f"{name}: provoz netece (in {subject['input_pps']} pps, "
            f"out {subject['output_pps']} pps)",
            label=name,
            subject=subject,
        )
    return Finding(
        Outcome.OK,
        f"{name}: provoz tece (in {subject['input_pps']} pps, "
        f"out {subject['output_pps']} pps)",
        label=name,
        subject=subject,
    )


def _compare_finding(
    name: str, baseline: dict[str, int], subject: dict[str, int], tolerance: float
) -> Finding:
    details = {"tolerance_percent": tolerance}
    drops = []
    for key in ("input_pps", "output_pps"):
        change = percent_change(baseline[key], subject[key])
        if change is None:
            continue
        details[f"{key}_change_percent"] = round(change, 1)
        if change < tolerance:
            drops.append(
                f"{key} kleslo o {abs(round(change))} % "
                f"({baseline[key]} -> {subject[key]})"
            )

    if drops:
        return Finding(
            Outcome.BROKEN,
            f"{name}: " + "; ".join(drops) + f", prah je {tolerance:.0f} %",
            label=name,
            baseline=baseline,
            subject=subject,
            details=details,
        )
    return Finding(
        Outcome.OK,
        f"{name}: provoz v toleranci {tolerance:.0f} %",
        label=name,
        baseline=baseline,
        subject=subject,
        details=details,
    )


@register
class TrafficCeasedCheck(Check):
    """Overi, ze na starem rozhrani provoz po migraci klesl k nule.

    Chyta zapomenute vypnuti a duplicitni forwarding. Default vypnuty -
    vyzaduje treti capture stareho boxu po migraci.
    """

    id = "traffic_ceased"
    title = "Utichnuti stareho rozhrani"
    mode = Mode.COMPARE
    requires = ("interfaces",)
    default_severity = Severity.ADVISORY

    def run(self, ctx: CheckContext) -> list[Finding]:
        names = _transit_interfaces(ctx)
        if not names:
            return [_no_transit_finding(ctx)]

        threshold = int(ctx.options(self.id)["max_residual_pps"])

        findings = []
        for name in names:
            subject = _rates(ctx.subject["interfaces"][name])
            baseline_data = (ctx.baseline or {}).get("interfaces", {}).get(name)
            if baseline_data is None:
                findings.append(
                    Finding(
                        Outcome.SKIP,
                        f"{name}: rozhrani neni v baseline snapshotu",
                        label=name,
                    )
                )
                continue

            baseline = _rates(baseline_data)
            if baseline["input_pps"] == 0 and baseline["output_pps"] == 0:
                findings.append(
                    Finding(
                        Outcome.SKIP,
                        f"{name}: v baseline zadny provoz, utichnuti nelze overit",
                        label=name,
                        baseline=baseline,
                        subject=subject,
                    )
                )
                continue

            residual = max(subject["input_pps"], subject["output_pps"])
            details = {"max_residual_pps": threshold, "residual_pps": residual}

            if residual > threshold:
                findings.append(
                    Finding(
                        Outcome.BROKEN,
                        f"{name}: stare rozhrani stale nese provoz "
                        f"({residual} pps, prah {threshold} pps)",
                        label=name,
                        baseline=baseline,
                        subject=subject,
                        details=details,
                    )
                )
            else:
                findings.append(
                    Finding(
                        Outcome.OK,
                        f"{name}: provoz utichl ({residual} pps)",
                        label=name,
                        baseline=baseline,
                        subject=subject,
                        details=details,
                    )
                )
        return findings
