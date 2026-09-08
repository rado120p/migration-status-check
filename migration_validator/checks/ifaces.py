"""Checky nad rozhranimi.

Counter-based checky bezi jen na tranzitnich rozhranich. Na internich davaji
SKIP, ne WARN - SKIP znamena "tenhle test sem nepatri", WARN "neco je spatne".
Kdyby interni rozhrani trvale svitila oranzove, operator si zvykne vystup
preskakovat.
"""

from __future__ import annotations

from typing import Any

from migration_validator.checks.base import Check, CheckContext, Mode
from migration_validator.checks.baseline import UNKNOWN, suffix, unchanged_or
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


def is_physical(interface: str) -> bool:
    """Fyzicke rozhrani (bez unitu). Unity error countery nenesou -
    radek 'bez chyb' na unitu tvrdi mereni, ktere neprobehlo."""
    return "." not in interface


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


def scope_interfaces(ctx: CheckContext) -> list[str]:
    """Rozhrani, jejichz radky patri do tohoto scopu.

    Layer1 scope nese fyzicky port; service scope s L1 rodicem jen unity
    (radky portu ma jeho L1 blok - deduplikace ze specu); sluzba bez L1
    rodice vse jako drive.
    """
    names = sorted(ctx.subject.get("interfaces", {}))
    if ctx.scope.kind == "layer1":
        return [name for name in names if is_physical(name)]
    # Netranzitni rodic (lo0/irb) zadny L1 blok nema, jeho radky musi
    # zustat ve sluzbe - builder L1 scope staví jen pro tranzitni porty.
    if any(is_transit(p) for p in ctx.scope.selectors.physical_interfaces):
        return [name for name in names if not is_physical(name)]
    return names


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


def _l3_link_without_transit(ctx: CheckContext) -> dict[str, Any] | None:
    """Vazba na L2 cast, kdyz scope sam tranzit nema.

    Counter checky se u takove sluzby nestehuji do SKIPu - mereni probiha
    v L2 bloku a report na nej ma ukazat, ne tvrdit, ze neni co merit.
    """
    link = ctx.link
    if link and link["role"] == "l3" and not _transit_interfaces(ctx):
        return link
    return None


@register
class InterfaceStateCheck(Check):
    """Stav rozhrani (admin/oper).

    BOTH od 2026-09-08 (R-3): down v obou = PASS se znackou; bez baseline
    zaznamu (nesparovane rozhrani) chova se jako drive.
    """

    id = "interface_state"
    title = "Stav rozhrani"
    label = "Interface status"
    mode = Mode.BOTH
    requires = ("interfaces",)
    default_severity = Severity.CRITICAL
    layer1 = True

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

        names = scope_interfaces(ctx)
        layer1_scope = ctx.scope.kind == "layer1"
        baseline_ifaces = (ctx.baseline or {}).get("interfaces", {})

        findings = []
        for name in names:
            data = interfaces[name]
            was = baseline_ifaces.get(name)
            for label, key in (
                ("Interface admin status", "admin_status"),
                ("Interface operational status", "oper_status"),
            ):
                state = str(data.get(key, UNKNOWN))
                was_state = str(was.get(key, UNKNOWN)) if was is not None else None
                ok = state == "up"
                outcome = Outcome.OK if ok else unchanged_or(
                    Outcome.BROKEN, ctx, "interfaces",
                    same=state != UNKNOWN and was_state == state,
                )
                findings.append(
                    Finding(
                        outcome,
                        f"{name}: {key} {state}{suffix(outcome)}",
                        label=label if layer1_scope else qualified(label, name),
                        value=state.capitalize(),
                        baseline_value=was_state.capitalize() if was_state is not None else None,
                        subject={key: state},
                    )
                )
        return findings


@register
class InterfaceErrorsCheck(Check):
    id = "interface_errors"
    title = "Chybove countery rozhrani"
    label = "Interface errors"
    mode = Mode.BOTH
    requires = ("interfaces",)
    default_severity = Severity.ADVISORY
    layer1 = True

    def run(self, ctx: CheckContext) -> list[Finding]:
        link = _l3_link_without_transit(ctx)
        if link is not None:
            peers = link.get("peers") or []
            peer = ", ".join(p["interface"] for p in peers)
            blocks = "bloky nize" if len(peers) > 1 else "blok nize"
            return [
                Finding(
                    outcome=Outcome.INFO,
                    message=f"errors/traffic se meri na L2 casti ({peer})",
                    label="Interface errors / traffic",
                    value=f"mereno na L2 ({peer}) - viz {blocks}",
                    # Radek jen odkazuje na L2 blok, sam nic nemeri - z
                    # definice se neporovnava (ne "bez baseline" v reportu).
                    compared=False,
                )
            ]

        # Chybove countery nese jen fyzicky port - v service scopu s L1
        # rodicem ho hlasi ten L1 blok (deduplikace ze specu), tady by radek
        # jen zdvojoval. Netranzitni rodic (lo0/irb) zadny L1 blok nema, jeho
        # radky musi zustat ve sluzbe.
        if ctx.scope.kind != "layer1" and any(
            is_transit(p) for p in ctx.scope.selectors.physical_interfaces
        ):
            return []

        layer1_scope = ctx.scope.kind == "layer1"
        transit_names = _transit_interfaces(ctx)
        # Chybove countery nese jen fyzicke rozhrani - scope_interfaces samo
        # o sobe fyzicke od logickych oddeli jen na layer1 scopu.
        names = [name for name in scope_interfaces(ctx) if is_transit(name) and is_physical(name)]
        if not names:
            if transit_names:
                # Tranzitni rozhrani jsou, ale jen logicke unity - nemaji
                # chybove countery. Nemuze se pouzit _no_transit_finding, jejiz
                # veta "neni tranzitni rozhrani" by byla nepravda.
                listed = ", ".join(transit_names)
                return [Finding(
                    outcome=Outcome.SKIP,
                    message=f"chybove countery nese jen fyzicke rozhrani, ve scope jsou jen unity ({listed})",
                    value="jen unity",
                )]
            else:
                # Zadne tranzitni rozhrani - pouzij sdilenou zpravu od _no_transit_finding
                return [_no_transit_finding(ctx)]

        findings = []
        for name in names:
            data = ctx.subject["interfaces"][name]
            label = "Interface errors" if layer1_scope else qualified("Interface errors", name)
            raw_counters = {
                key: data[key]
                for key in ("input_errors", "output_errors", "framing_errors")
                if key in data
            }
            if not raw_counters:
                findings.append(
                    Finding(
                        Outcome.DEGRADED,
                        f"{name}: chybove countery nebyly zmereny (rozhrani nevraci error countery)",
                        label=label,
                        value="nezmereno",
                        compared=False,
                    )
                )
                continue
            counters = {key: int(value) for key, value in raw_counters.items()}
            findings.append(_errors_finding(name, label, counters, _baseline_counters(ctx, name)))
        return findings


def _baseline_counters(ctx: CheckContext, name: str) -> dict[str, int] | None:
    """Countery tehoz rozhrani v baseline; None bez baseline nebo bez
    zaznamu (jmeno se migraci meni, ge- -> et-)."""
    data = (ctx.baseline or {}).get("interfaces", {}).get(name)
    if not data:
        return None
    counters = {
        key: int(data[key])
        for key in ("input_errors", "output_errors", "framing_errors")
        if key in data
    }
    return counters or None


def _nonzero_text(counters: dict[str, int]) -> str:
    return ", ".join(f"{key}={value}" for key, value in counters.items() if value)


def _errors_finding(
    name: str, label: str, counters: dict[str, int], baseline: dict[str, int] | None
) -> Finding:
    """Countery jsou kumulativni od bootu, takze bez baseline vadi kazda
    nenula. S baseline vadi jen prirustek - stejne (nebo nizsi, po rebootu)
    hodnoty jsou stare chyby, ne dusledek migrace: PASS 'stejne jako
    baseline' (rozhodnuti 2026-09-07)."""
    baseline_value = _nonzero_text(baseline) or "bez chyb" if baseline else None
    if sum(counters.values()) == 0:
        return Finding(
            Outcome.OK, f"{name}: bez chyb", label=label, value="bez chyb",
            subject=counters, baseline_value=baseline_value, baseline=baseline,
        )
    if baseline is not None:
        grown = {
            key: value for key, value in counters.items() if value > baseline.get(key, 0)
        }
        if not grown:
            return Finding(
                Outcome.OK,
                f"{name}: chybove countery stejne jako baseline ({_nonzero_text(counters)})",
                label=label, value=_nonzero_text(counters),
                subject=counters, baseline_value=baseline_value, baseline=baseline,
            )
        delta = ", ".join(f"{key}={baseline.get(key, 0)} -> {value}" for key, value in grown.items())
        return Finding(
            Outcome.BROKEN,
            f"{name}: chybove countery od baseline vzrostly ({delta})",
            label=label, value=_nonzero_text(counters),
            subject=counters, baseline_value=baseline_value, baseline=baseline,
        )
    detail = _nonzero_text(counters)
    return Finding(
        Outcome.BROKEN,
        f"{name}: chybove countery nenulove ({detail})",
        label=label, value=detail, subject=counters,
    )


@register
class InterfaceTrafficCheck(Check):
    id = "interface_traffic"
    title = "Datovost rozhrani"
    label = "Interface traffic"
    mode = Mode.BOTH
    requires = ("interfaces",)
    default_severity = Severity.ADVISORY
    layer1 = True

    def run(self, ctx: CheckContext) -> list[Finding]:
        if _l3_link_without_transit(ctx) is not None:
            # radek by duplikoval INFO odkaz z interface_errors
            return []

        names = [name for name in scope_interfaces(ctx) if is_transit(name)]
        if not names:
            return [_no_transit_finding(ctx)]

        layer1_scope = ctx.scope.kind == "layer1"
        options = ctx.options(self.id)
        tolerance = float(options["tolerance_percent"])
        require_nonzero = bool(options["require_nonzero"])

        findings = []
        for name in names:
            subject = _rates(ctx.subject["interfaces"][name])
            baseline_data = (ctx.baseline or {}).get("interfaces", {}).get(name)
            baseline = _rates(baseline_data) if baseline_data else None

            for base_label, key in (
                ("Interface traffic in", "input_pps"),
                ("Interface traffic out", "output_pps"),
            ):
                label = base_label if layer1_scope else qualified(base_label, name)
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
    report ve sloupci ZMENA nevypise nic. `label` prichazi hotovy - jestli
    ponese jmeno rozhrani v zavorce, rozhoduje volajici podle scopu.

    require_nonzero se uplatni jen bez baseline. S baseline 0 vraci
    percent_change None (deleni nulou) a 0 -> 0 je zamerne OK, aby sluzba,
    ktera nefungovala uz pred migraci, nesvitila FAIL.
    """
    value = f"{subject} pps"

    if baseline is None:
        broken = require_nonzero and subject == 0
        return Finding(
            Outcome.BROKEN if broken else Outcome.OK,
            f"{name}: {key} {subject} pps" + (", ocekavan nenulovy provoz" if broken else ""),
            label=label,
            value=value,
            subject={key: subject},
        )

    change = percent_change(baseline, subject)
    delta = None if change is None else f"{change:+.0f} %"
    broken = change is not None and change < tolerance

    if change is None:
        message = (
            f"{name}: {key} stejne jako baseline (0 pps)"
            if subject == 0
            else f"{name}: {key} v toleranci {tolerance:.0f} %"
        )
    elif broken:
        message = (
            f"{name}: {key} kleslo o {abs(round(change))} % "
            f"({baseline} -> {subject}), prah je {tolerance:.0f} %"
        )
    else:
        message = f"{name}: {key} v toleranci {tolerance:.0f} %"

    return Finding(
        Outcome.BROKEN if broken else Outcome.OK,
        message,
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
    layer1 = True

    def run(self, ctx: CheckContext) -> list[Finding]:
        if _l3_link_without_transit(ctx) is not None:
            # radek by duplikoval INFO odkaz z interface_errors
            return []

        names = [name for name in scope_interfaces(ctx) if is_transit(name)]
        if not names:
            return [_no_transit_finding(ctx)]

        layer1_scope = ctx.scope.kind == "layer1"
        threshold = int(ctx.options(self.id)["max_residual_pps"])

        findings = []
        for name in names:
            label = (
                "Interface traffic ceased"
                if layer1_scope
                else qualified("Interface traffic ceased", name)
            )
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
