"""Terminalovy vystup.

Vychozi vypis je souhrn, radek na sluzbu a plny blok u sluzeb se stavem
WARN nebo FAIL. --detail rozbali bloky u vsech vcetne PASS.

Sekce NESPAROVANO se vypisuje vzdy, i kdyz je vsechno ostatni zelene, a
filtrovani se na ni nevztahuje - je to hlavni pojistka proti prehlednuti
nezmigrovane sluzby.
"""

from __future__ import annotations

from dataclasses import replace

from migration_validator.models.result import RunResult, Status
from migration_validator.reporting.view import Section, ServiceView, build_view, change_text

SYMBOL = {
    Status.PASS: "PASS",
    Status.WARN: "WARN",
    Status.FAIL: "FAIL",
    Status.SKIP: "SKIP",
}

FAMILY_TITLE = {4: "IPv4", 6: "IPv6"}


def filter_result(
    result: RunResult,
    *,
    text: str | None = None,
    statuses: set[Status] | None = None,
) -> RunResult:
    """Vrati kopii vysledku s profiltrovanymi scopy. Unmatched zustava cely."""
    scopes = list(result.scopes)

    if text:
        needle = text.lower()
        scopes = [
            scope
            for scope in scopes
            if needle in scope.scope_id.lower()
            or needle in str(scope.key.get("description", "")).lower()
        ]

    if statuses:
        scopes = [scope for scope in scopes if scope.status in statuses]

    return replace(result, scopes=scopes)


def _section_header(section: Section, width: int) -> str:
    """Nadpis sekce. U vice adres jedne rodiny je vypise vsechny."""
    if section.family is None:
        return ""
    title = FAMILY_TITLE[section.family]
    addresses = ", ".join(section.addresses) or "-"
    gateway = f"   VGW {', '.join(section.virtual_gw)}" if section.virtual_gw else ""
    text = f" -- {title}  {addresses}{gateway} "
    return text + "-" * max(0, width - len(text))


def _block(view: ServiceView, has_baseline: bool) -> list[str]:
    """Blok jedne sluzby. Sirky se pocitaji ze VSECH radku bloku.

    Kdyby si je pocitala kazda sekce zvlast, neseden by na spolecnou
    oddelovaci caru, ktera se kresli jednou. Neorezava se: orezana IPv6
    adresa nebo jmeno RIB jsou horsi nez nic.
    """
    rows = [row for section in view.sections for row in section.rows]
    changes = {id(row): change_text(row, has_baseline) for row in rows}

    subject_port = view.subject_interfaces[0] if view.subject_interfaces else "-"
    baseline_port = view.baseline_interfaces[0] if view.baseline_interfaces else "-"

    # Nadpisy sloupcu se do sirek zapocitavaji taky - jinak by ramec bloku
    # a oddelovaci cara byly kratsi nez hlavicka a vypis by se rozjel.
    label_title = "CHECK"
    value_title = f"POST ({subject_port})" if has_baseline else "HODNOTA"
    change_title = f"ZMENA PROTI {baseline_port}"

    label_width = max([len(row.label) for row in rows] + [len(label_title)])
    value_width = max([len(row.value) for row in rows] + [len(value_title)])
    change_width = max([len(text) for text in changes.values()] + [len(change_title)])

    def line(status: str, label: str, value: str, change: str) -> str:
        text = f" {status:<4} | {label:<{label_width}} : {value:<{value_width}}"
        if not has_baseline:
            return text.rstrip()
        return f"{text} | {change}".rstrip()

    table_width = 1 + 4 + 3 + label_width + 3 + value_width
    if has_baseline:
        table_width += 3 + change_width

    ports = (
        f"{baseline_port} -> {subject_port}" if has_baseline else subject_port
    )
    instance = view.routing_instance or "-"

    # Popisek sluzby je volny text bez horni meze delky (description,
    # routing instance) - ramec musi obalit i tenhle radek, ne jen tabulku
    # sloupcu. Jinak by dlouhy nazev sluzby prerostl "=" caru.
    header_line = (
        f" {SYMBOL[view.status].strip():<4}  {view.description}   "
        f"{view.service_type}   {ports}   RI: {instance}"
    )
    width = max(table_width, len(header_line))

    lines = [
        "=" * width,
        header_line,
        "=" * width,
        line("STAV", label_title, value_title, change_title),
    ]
    separator = f" {'-'*4}-+-{'-'*label_width}-+-{'-'*value_width}"
    if has_baseline:
        separator += f"-+-{'-'*change_width}"
    lines.append(separator)

    for section in view.sections:
        header = _section_header(section, width)
        if header:
            lines.append("")
            lines.append(header)
        for row in section.rows:
            lines.append(
                line(SYMBOL[row.status].strip(), row.label, row.value, changes[id(row)])
            )

    lines.append("")
    return lines


def render(result: RunResult, *, detail: bool = False) -> str:
    lines: list[str] = []

    subject = result.subject
    baseline = result.baseline
    has_baseline = baseline is not None
    if has_baseline:
        lines.append(
            f"Migrace: {baseline['address']} ({baseline['phase']}) -> "
            f"{subject['address']} ({subject['phase']})"
        )
    else:
        lines.append(f"Validace: {subject['address']} ({subject['phase']})")
    lines.append("")

    summary = result.summary
    lines.append(
        f"  {summary['pass']} PASS   {summary['warn']} WARN   "
        f"{summary['fail']} FAIL   {summary['skip']} SKIP"
    )
    lines.append(
        f"  Sparovano {summary['scopes_matched']} sluzeb, "
        f"{summary['unmatched_baseline']} nesparovana v baseline, "
        f"{summary['unmatched_subject']} nesparovane v subject"
    )
    lines.append("")

    views = [(scope, build_view(scope)) for scope in result.scopes]

    lines.append(
        f"{'STAV':<5} {'SLUZBA':<26} {'TYP':<9} {'STARY PORT':<13} "
        f"{'NOVY PORT':<13} {'RI':<17} NALEZ"
    )
    for _scope, view in views:
        old_port = view.baseline_interfaces[0] if view.baseline_interfaces else "-"
        new_port = view.subject_interfaces[0] if view.subject_interfaces else "-"
        lines.append(
            f"{SYMBOL[view.status]:<5} {view.description:<26} {view.service_type:<9} "
            f"{old_port:<13} {new_port:<13} {view.routing_instance or '-':<17} "
            f"{view.worst_message}".rstrip()
        )
    lines.append("")

    # Rozbaluje stav, ne interaktivita: v terminalu se neklikne, ale detail
    # je potreba prave tam, kde je neco rozbite. --detail rozbali i PASS.
    for _scope, view in views:
        if detail or view.status is not Status.PASS:
            lines.extend(_block(view, has_baseline))

    lines.append("NESPAROVANO")
    if not result.unmatched["baseline"] and not result.unmatched["subject"]:
        lines.append("  (nic)")
    for side in ("baseline", "subject"):
        for item in result.unmatched[side]:
            label = item.get("description") or item["scope_id"]
            service_type = item.get("service_type") or "-"
            lines.append(f"  {side:<9} {label:<32.32} ({service_type})  {item['reason']}")

    return "\n".join(lines) + "\n"
