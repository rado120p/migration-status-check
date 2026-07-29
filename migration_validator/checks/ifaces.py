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


def qualified(label: str, interface: str) -> str:
    """Popisek radku nesouci jmeno rozhrani.

    Kazdy scope drzi fyzicke i logicke rozhrani, takze bez jmena ma kazdy
    blok dvojice radku se stejnym popiskem, jinymi hodnotami a
    protichudnymi sloupci ZMENA - a neni poznat, ktere rozhrani je ktere.
    Zavorka je stejny tvar, jakym AR-5b kvalifikuje adresu v ramci rodiny;
    tam resil vzacny pripad dvou rozsahu, tady ten univerzalni.
    """
    return f"{label} ({interface})"


def _transit_interfaces(ctx: CheckContext) -> list[str]:
    return sorted(name for name in ctx.subject.get("interfaces", {}) if is_transit(name))


def _no_transit_finding(ctx: CheckContext) -> Finding:
    """Popisek si radek vezme od checku - stejny duvod hlasi tri ruzne
    countery a kazdy ma vlastni jmeno sloupce."""
    names = sorted(ctx.subject.get("interfaces", {}))
    listed = ", ".join(names) if names else "zadne rozhrani"
    return Finding(
        outcome=Outcome.SKIP,
        message=f"neni tranzitni rozhrani ({listed}), counter check se preskakuje",
        value="netranzitni rozhrani",
    )


@register
class InterfaceStateCheck(Check):
    id = "interface_state"
    title = "Stav rozhrani"
    label = "Interface status"
    mode = Mode.STATE
    requires = ("interfaces",)
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        interfaces: dict[str, Any] = ctx.subject.get("interfaces", {})
        if not interfaces:
            return [
                Finding(
                    Outcome.SKIP,
                    "pro tento scope nejsou data o rozhranich",
                    value="bez dat",
                )
            ]

        findings = []
        for name in sorted(interfaces):
            data = interfaces[name]
            for label, key in (
                ("Interface admin status", "admin_status"),
                ("Interface operational status", "oper_status"),
            ):
                state = str(data.get(key, "unknown"))
                ok = state == "up"
                findings.append(
                    Finding(
                        Outcome.OK if ok else Outcome.BROKEN,
                        f"{name}: {key} {state}",
                        label=qualified(label, name),
                        value=state.capitalize(),
                        subject={key: state},
                    )
                )
        return findings


@register
class InterfaceErrorsCheck(Check):
    id = "interface_errors"
    title = "Chybove countery rozhrani"
    label = "Interface errors"
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
            label = qualified("Interface errors", name)
            total = sum(counters.values())
            if total == 0:
                findings.append(
                    Finding(
                        Outcome.OK,
                        f"{name}: bez chyb",
                        label=label,
                        value="bez chyb",
                        subject=counters,
                    )
                )
            else:
                detail = ", ".join(f"{key}={value}" for key, value in counters.items() if value)
                findings.append(
                    Finding(
                        Outcome.BROKEN,
                        f"{name}: chybove countery nenulove ({detail})",
                        label=label,
                        value=detail,
                        subject=counters,
                    )
                )
        return findings


@register
class InterfaceTrafficCheck(Check):
    id = "interface_traffic"
    title = "Datovost rozhrani"
    label = "Interface traffic"
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
            baseline = _rates(baseline_data) if baseline_data else None

            for label, key in (
                ("Interface traffic in", "input_pps"),
                ("Interface traffic out", "output_pps"),
            ):
                findings.append(
                    _traffic_finding(
                        name, label, key, subject[key],
                        baseline[key] if baseline else None,
                        tolerance, require_nonzero,
                    )
                )
        return findings


def _rates(data: dict[str, Any]) -> dict[str, int]:
    return {
        "input_pps": int(data.get("input_pps", 0)),
        "output_pps": int(data.get("output_pps", 0)),
    }


def _traffic_finding(
    name: str,
    label: str,
    key: str,
    subject: int,
    baseline: int | None,
    tolerance: float,
    require_nonzero: bool,
) -> Finding:
    """Jeden smer provozu = jeden radek reportu.

    Bez baseline se hodnoti jen absolutni hodnota; delta zustava None a
    report ve sloupci ZMENA nevypise nic.
    """
    label = qualified(label, name)
    value = f"{subject} pps"

    if baseline is None:
        broken = require_nonzero and subject == 0
        return Finding(
            Outcome.BROKEN if broken else Outcome.OK,
            f"{name}: {key} {subject} pps",
            label=label,
            value=value,
            subject={key: subject},
        )

    change = percent_change(baseline, subject)
    delta = None if change is None else f"{change:+.0f} %"
    broken = change is not None and change < tolerance

    return Finding(
        Outcome.BROKEN if broken else Outcome.OK,
        (
            f"{name}: {key} kleslo o {abs(round(change))} % "
            f"({baseline} -> {subject}), prah je {tolerance:.0f} %"
            if broken
            else f"{name}: {key} v toleranci {tolerance:.0f} %"
        ),
        label=label,
        value=value,
        baseline_value=f"{baseline} pps",
        delta=delta,
        baseline={key: baseline},
        subject={key: subject},
        details={"tolerance_percent": tolerance},
    )


@register
class TrafficCeasedCheck(Check):
    """Overi, ze na starem rozhrani provoz po migraci klesl k nule.

    Chyta zapomenute vypnuti a duplicitni forwarding. Default vypnuty -
    vyzaduje treti capture stareho boxu po migraci.
    """

    id = "traffic_ceased"
    title = "Utichnuti stareho rozhrani"
    label = "Interface traffic ceased"
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
            label = qualified("Interface traffic ceased", name)
            subject = _rates(ctx.subject["interfaces"][name])
            baseline_data = (ctx.baseline or {}).get("interfaces", {}).get(name)
            if baseline_data is None:
                findings.append(
                    Finding(
                        Outcome.SKIP,
                        f"{name}: rozhrani neni v baseline snapshotu",
                        label=label,
                        value="bez baseline",
                    )
                )
                continue

            baseline = _rates(baseline_data)
            # Compare check bez baseline_value hlasi ve sloupci ZMENA 'bez
            # baseline' - a tenhle check bez baseline vubec nebezi, takze by
            # to byla hlaska, ktera nemuze byt pravda.
            previous = f"{max(baseline['input_pps'], baseline['output_pps'])} pps"
            if baseline["input_pps"] == 0 and baseline["output_pps"] == 0:
                findings.append(
                    Finding(
                        Outcome.SKIP,
                        f"{name}: v baseline zadny provoz, utichnuti nelze overit",
                        label=label,
                        value="bez provozu v baseline",
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
                        label=label,
                        value=f"{residual} pps",
                        baseline_value=previous,
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
                        label=label,
                        value=f"{residual} pps",
                        baseline_value=previous,
                        baseline=baseline,
                        subject=subject,
                        details=details,
                    )
                )
        return findings
