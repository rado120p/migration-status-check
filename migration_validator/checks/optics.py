"""Opticke urovne a alarmy - jen Layer1 scopy.

Urovne se posuzuji deltou proti baseline (prahy modulu uz vyhodnotil box
sam - to jsou alarm/warn flagy). Alarm radky se tisknou JEN zvednute;
tichy port ma jeden souhrnny radek, stejny vzor jako Interface errors.

Nepripojeny port hlasi rx/tx jako -inf (skutecne chovani krabice, ne
chyba fixture). Delta se pocita jen kdyz jsou konecne obe strany -
z nekonecna by vysel nan/inf a DEGRADED z aritmetiky. Nekonecna uroven
sama je ale BROKEN: 'RX -Inf dBm' neni "v toleranci", je to port bez
svetla (pozadavek z lab testovani 2026-08-13; do te doby verdikt nesl
jen alarms check a levels radek matl PASSem).
"""

from __future__ import annotations

import math
from typing import Any

from migration_validator.checks.base import Check, CheckContext, Mode
from migration_validator.checks.baseline import suffix, unchanged_or
from migration_validator.checks.registry import register
from migration_validator.models.result import Finding, Outcome, Severity


def _optics_label(base: str, name: str, lane: int | None, port: str | None) -> str:
    parts = []
    if name != port:
        parts.append(name)  # clen LAGu - jmeno je pointa radku
    if lane is not None:
        parts.append(f"lane {lane}")
    return f"{base} ({' '.join(parts)})" if parts else base


def _port(ctx: CheckContext) -> str | None:
    """Port L1 scopu. None na device scope (applies_to ho pousti - vsechny
    checky bezi na cely device - ale zadny konkretni port nenese)."""
    return ctx.scope.selectors.interfaces[0] if ctx.scope.selectors.interfaces else None


def _ports(ctx: CheckContext) -> list[str]:
    port = _port(ctx)
    return [name for name in [port, *sorted(ctx.scope.selectors.lag_members)] if name]


def _fmt(power: float | None) -> str:
    """Junos-styl token pro nekonecno: '-Inf dBm', ne '-inf dBm' z f-stringu."""
    if power is None:
        return "?"
    if not math.isfinite(power):
        return f"{'-Inf' if power < 0 else 'Inf'} dBm"
    return f"{power:.2f} dBm"


@register
class OpticalLevelsCheck(Check):
    id = "interface_optics_levels"
    title = "Opticke urovne"
    label = "Interface optical levels"
    mode = Mode.BOTH
    requires = ("optics",)
    service_types = frozenset()  # nikdy na service scopu
    layer1 = True
    # CRITICAL kvuli -Inf urovnim (BROKEN => FAIL). Delta pres toleranci
    # je DEGRADED a ta zustava WARN pri jakekoli severity (derive_status).
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        optics: dict[str, Any] = ctx.subject.get("optics", {})
        baseline_optics: dict[str, Any] = (ctx.baseline or {}).get("optics", {})
        tolerance = float(ctx.options(self.id)["tolerance_db"])
        port = _port(ctx)

        findings: list[Finding] = []
        for name in _ports(ctx):
            data = optics.get(name)
            if data is None:
                findings.append(
                    Finding(
                        Outcome.SKIP,
                        f"{name}: rozhrani nevraci opticka data",
                        # Jmeno vzdy, i kdyz name == port: SKIP radek stoji
                        # vedle lane radku clenu LAGu a bez jmena nebylo
                        # poznat, ze "bez optiky" mluvi o rodici (ae0).
                        label=f"{self.label} ({name})",
                        value="bez optiky",
                    )
                )
                continue
            baseline_lanes = {
                lane.get("lane"): lane
                for lane in baseline_optics.get(name, {}).get("lanes", [])
            }
            for lane in data["lanes"]:
                findings.append(
                    _level_finding(
                        _optics_label(self.label, name, lane["lane"], port),
                        name,
                        lane,
                        baseline_lanes.get(lane["lane"]),
                        tolerance,
                        ctx,
                    )
                )
        return findings


def _dark_sides(lane: dict[str, Any]) -> list[str]:
    """Strany s nekonecnou urovni ('RX', 'TX') - port bez svetla."""
    return [
        tag
        for key, tag in (("rx_power_dbm", "RX"), ("tx_power_dbm", "TX"))
        if lane.get(key) is not None and not math.isfinite(lane[key])
    ]


def _level_finding(
    label: str,
    name: str,
    lane: dict[str, Any],
    baseline_lane: dict[str, Any] | None,
    tolerance: float,
    ctx: CheckContext,
) -> Finding:
    value = f"RX {_fmt(lane['rx_power_dbm'])} / TX {_fmt(lane['tx_power_dbm'])}"
    dark = _dark_sides(lane)
    if baseline_lane is None:
        return Finding(
            Outcome.BROKEN if dark else Outcome.OK,
            (
                f"{name}: {'/'.join(dark)} bez svetla ({value})"
                if dark
                else f"{name}: {value}"
            ),
            label=label, value=value,
            # Baseline nema tuto lane (jina inventory, jiny pocet lanes) -
            # radek se z definice neporovnava, ne "bez baseline" (R-4).
            compared=False,
            subject={"rx_power_dbm": lane["rx_power_dbm"],
                     "tx_power_dbm": lane["tx_power_dbm"]},
        )

    deltas = []
    degraded = False
    for key, tag in (("rx_power_dbm", "RX"), ("tx_power_dbm", "TX")):
        now, before = lane.get(key), baseline_lane.get(key)
        if now is None or before is None:
            continue
        # -Inf na jedne (nebo obou) stranach: delta by z nekonecna vyrobila
        # nan/inf, takze se nepocita - verdikt BROKEN nese vetev `dark`.
        if not (math.isfinite(now) and math.isfinite(before)):
            continue
        diff = now - before
        deltas.append(f"{tag} {diff:+.1f} dB")
        if abs(diff) > tolerance:
            degraded = True

    baseline_value = (
        f"RX {_fmt(baseline_lane['rx_power_dbm'])}"
        f" / TX {_fmt(baseline_lane['tx_power_dbm'])}"
    )
    if dark:
        outcome = unchanged_or(
            Outcome.BROKEN, ctx, "optics", same=_dark_sides(baseline_lane) == dark
        )
        message = f"{name}: {'/'.join(dark)} bez svetla ({value}){suffix(outcome)}"
    elif degraded:
        outcome = Outcome.DEGRADED
        message = (
            f"{name}: uroven se posunula o vic nez {tolerance:.1f} dB"
            f" ({', '.join(deltas)})"
        )
    else:
        outcome = Outcome.OK
        message = f"{name}: urovne v toleranci {tolerance:.1f} dB"
    return Finding(
        outcome,
        message,
        label=label,
        value=value,
        baseline_value=baseline_value,
        delta=", ".join(deltas) or None,
        details={"tolerance_db": tolerance},
    )


def _raised(data: dict[str, Any] | None) -> list[tuple[int | None, str, Outcome]]:
    """Zvednute alarmy/warningy jedne strany (subjekt nebo baseline)."""
    result: list[tuple[int | None, str, Outcome]] = []
    if data is None:
        return result
    for lane in data["lanes"]:
        for tag, is_on in lane["alarms"].items():
            if is_on:
                result.append((lane["lane"], tag, Outcome.BROKEN))
        for tag, is_on in lane["warnings"].items():
            if is_on:
                result.append((lane["lane"], tag, Outcome.DEGRADED))
    return result


@register
class OpticalAlarmsCheck(Check):
    id = "interface_optics_alarms"
    title = "Opticke alarmy"
    label = "Interface optical alarms"
    # BOTH: shodny zvedly alarm v subjektu i zmerene baseline je PASS se
    # znackou (Outcome.UNCHANGED), ne tiche FAIL/WARN.
    mode = Mode.BOTH
    requires = ("optics",)
    service_types = frozenset()
    layer1 = True
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        optics: dict[str, Any] = ctx.subject.get("optics", {})
        baseline_optics: dict[str, Any] = (ctx.baseline or {}).get("optics", {})
        port = _port(ctx)

        findings: list[Finding] = []
        for name in _ports(ctx):
            data = optics.get(name)
            if data is None:
                findings.append(
                    Finding(
                        Outcome.SKIP,
                        f"{name}: rozhrani nevraci opticka data",
                        # Jmeno vzdy, i kdyz name == port: SKIP radek stoji
                        # vedle lane radku clenu LAGu a bez jmena nebylo
                        # poznat, ze "bez optiky" mluvi o rodici (ae0).
                        label=f"{self.label} ({name})",
                        value="bez optiky",
                    )
                )
                continue
            raised = _raised(data)
            baseline_data = baseline_optics.get(name)
            baseline_raised = _raised(baseline_data)
            # Co baseline pro tento port skutecne zmerila - pouziva se jako
            # baseline_value kdykoli konkretni tag/lane neni "same" (nebo na
            # tichem OK radku). Renderer tiskne "bez baseline" u kazdeho
            # ne-SKIP radku BOTH checku s baseline_value=None, kdyz baseline
            # beh existuje - takze None patri jen portu, ktery baseline vubec
            # nezmerila.
            baseline_summary = (
                "bez alarmu" if baseline_data is not None and not baseline_raised
                else (
                    ", ".join(sorted({tag for _, tag, _ in baseline_raised}))
                    if baseline_data is not None
                    else None
                )
            )
            # Baseline optics nema tenhle port vubec (jina inventory, jiny
            # beh) - radky se z definice neporovnavaji, ne "bez baseline"
            # (R-4, mirror _level_finding); baseline_value zustava tam, kde
            # port skutecne zmereny byl (baseline_summary je None jen kdyz
            # baseline_data is None, coz uz kompared=False pokryva).
            port_compared = baseline_data is not None
            if not raised:
                findings.append(
                    Finding(
                        Outcome.OK,
                        f"{name}: bez optickych alarmu",
                        label=_optics_label(self.label, name, None, port),
                        value="bez alarmu",
                        baseline_value=baseline_summary,
                        compared=port_compared,
                    )
                )
                continue
            # Klic nese i Outcome (BROKEN/DEGRADED), ne jen (lane, tag) -
            # tag, ktery v baseline byl jen warning a ted je alarm (eskalace
            # severity), neni "stejny stav" a nesmi se schovat za UNCHANGED.
            baseline_triples = {(lane_no, tag, o) for lane_no, tag, o in baseline_raised}
            for lane_no, tag, alarm_outcome in raised:
                same = (lane_no, tag, alarm_outcome) in baseline_triples
                outcome = unchanged_or(alarm_outcome, ctx, "optics", same=same)
                findings.append(
                    Finding(
                        outcome,
                        f"{name}: {tag} je aktivni{suffix(outcome)}",
                        label=_optics_label(self.label, name, lane_no, port),
                        value=tag,
                        baseline_value=tag if same else baseline_summary,
                        compared=port_compared,
                    )
                )
        return findings
