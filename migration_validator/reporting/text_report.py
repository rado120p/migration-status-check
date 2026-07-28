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

    # Sirky sloupcu se pocitaji z realnych dat, stejne jako v _block() -
    # napevno dane sirky prekypuji na skutecnych nazvech (dlouhy nazev
    # sluzby/RI z laborky), coz posune vsechny sloupce napravo a rozjede
    # zarovnani. Bez orezavani - orezany nazev je horsi nez nic.
    rows = [
        (
            SYMBOL[view.status],
            view.description,
            view.service_type,
            view.baseline_interfaces[0] if view.baseline_interfaces else "-",
            view.subject_interfaces[0] if view.subject_interfaces else "-",
            view.routing_instance or "-",
            view.worst_message,
        )
        for _scope, view in views
    ]

    # STAV je uzavrena mnozina ctyrpismennych symbolu (viz SYMBOL) - napevno
    # dana sirka mu nikdy nemuze prerust, stejne jako sloupci STAV v _block().
    status_w = 5
    service_w = max([len(r[1]) for r in rows] + [len("SLUZBA")])
    type_w = max([len(r[2]) for r in rows] + [len("TYP")])
    old_port_w = max([len(r[3]) for r in rows] + [len("STARY PORT")])
    new_port_w = max([len(r[4]) for r in rows] + [len("NOVY PORT")])
    ri_w = max([len(r[5]) for r in rows] + [len("RI")])

    lines.append(
        f"{'STAV':<{status_w}} {'SLUZBA':<{service_w}} {'TYP':<{type_w}} "
        f"{'STARY PORT':<{old_port_w}} {'NOVY PORT':<{new_port_w}} {'RI':<{ri_w}} NALEZ"
    )
    for status, description, service_type, old_port, new_port, instance, message in rows:
        lines.append(
            f"{status:<{status_w}} {description:<{service_w}} {service_type:<{type_w}} "
            f"{old_port:<{old_port_w}} {new_port:<{new_port_w}} {instance:<{ri_w}} "
            f"{message}".rstrip()
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
    else:
        # Sirka se pocita z dat, ne napevno (byval "{:<32.32}") - realny nazev
        # sluzby z laborky umi byt delsi nez 32 znaku a orezany je presne to,
        # co ma NESPAROVANO zabranit prehlednout.
        rows = [
            (side, item.get("description") or item["scope_id"], item.get("service_type") or "-", item["reason"])
            for side in ("baseline", "subject")
            for item in result.unmatched[side]
        ]
        label_width = max(len(label) for _, label, _, _ in rows)
        for side, label, service_type, reason in rows:
            lines.append(f"  {side:<9} {label:<{label_width}} ({service_type})  {reason}")

    return "\n".join(lines) + "\n"
