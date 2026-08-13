"""Terminalovy vystup.

Vychozi vypis je souhrn, radek na sluzbu a plny blok u sluzeb se stavem
WARN nebo FAIL. --detail rozbali bloky u vsech vcetne PASS.

Sekce NESPAROVANO a NEZARAZENO se vypisuji vzdy, i kdyz je vsechno ostatni
zelene, a filtrovani se na ne nevztahuje - jsou to hlavni pojistky proti
prehlednuti nezmigrovane sluzby, respektive objektu bez prirazene sluzby.
"""

from __future__ import annotations

import os
import sys
from dataclasses import replace

from migration_validator.models.result import RunResult, Status, count_statuses
from migration_validator.models.scope import LAYER1_SERVICE_TYPE
from migration_validator.reporting.view import (
    Group,
    Section,
    ServiceView,
    build_view,
    change_text,
)

SYMBOL = {
    Status.PASS: "PASS",
    Status.WARN: "WARN",
    Status.FAIL: "FAIL",
    Status.SKIP: "SKIP",
    # INFO puvodne mapovalo na "" (hodnota bez verdiktu, prazdny STAV).
    # Revize s barvenim: 47 INFO radku v ostrem behu bez tokenu splyvalo
    # s okolim a nemelo se cim obarvit - token se tiskne a barvi cyan.
    Status.INFO: "INFO",
}

# Zakladni 8barevna paleta schvalne - funguje na tmavem i svetlem pozadi
# a nevyzaduje detekci schopnosti terminalu.
_ANSI = {
    Status.PASS: "\x1b[32m",
    Status.WARN: "\x1b[33m",
    Status.FAIL: "\x1b[31m",
    Status.SKIP: "\x1b[2m",
    Status.INFO: "\x1b[36m",
}
_RESET = "\x1b[0m"


def _colorize(status: Status, text: str, color: bool) -> str:
    """Obali text ANSI barvou statusu. Prazdny token se neobaluje."""
    if not color or not text:
        return text
    return f"{_ANSI[status]}{text}{_RESET}"


def _status_cell(status: Status, width: int, *, color: bool) -> str:
    """Status token doplneny mezerami na sirku sloupce.

    Padding se pocita z cisteho textu PRED obarvenim - escape sekvence
    maji nenulovy len(), takze format spec `:<w` by rozjel zarovnani.
    """
    plain = SYMBOL[status].strip()
    return _colorize(status, plain, color) + " " * (width - len(plain))


def use_color(
    *, force_on: bool = False, force_off: bool = False, stream=None
) -> bool:
    """Rozhodne, jestli report barvit.

    Poradi: --no-color > --color > autodetekce. Vypnuti vyhrava, aby se
    barvy daly vzdy zakazat i ve skriptu, ktery je jinde vynucuje.
    Autodetekce: stream (default stdout) je TTY a NO_COLOR neni nastavena na neprazdnou
    hodnotu (konvence no-color.org - prazdna hodnota se cte jako
    nenastavena).
    """
    if force_off:
        return False
    if force_on:
        return True
    if stream is None:
        stream = sys.stdout
    return stream.isatty() and not os.environ.get("NO_COLOR")


FAMILY_TITLE = {4: "IPv4", 6: "IPv6"}


def _l1_parent_ids(all_scopes, kept_ids):
    """L1 rodice zobrazenych sluzeb - rodic jde s ditetem, aby seskupeni
    po portech nezustalo bez hlavicky portu."""
    l1_by_port = {
        (scope.identity or {}).get("interfaces", ["?"])[0]: scope.scope_id
        for scope in all_scopes
        if (scope.key or {}).get("service_type") == LAYER1_SERVICE_TYPE
    }
    parents = set()
    for scope in all_scopes:
        if scope.scope_id not in kept_ids:
            continue
        for port in (scope.identity or {}).get("physical_interfaces") or []:
            if port in l1_by_port:
                parents.add(l1_by_port[port])
    return parents


def filter_result(
    result: RunResult,
    *,
    text: str | None = None,
    statuses: set[Status] | None = None,
) -> RunResult:
    """Vrati kopii vysledku s profiltrovanymi scopy.

    Souhrnne pocty se prepocitaji za vybranou mnozinu - jinak hlavicka
    tvrdi neco jineho nez tabulka hned pod ni. Unmatched a unassigned se ale
    NEprepocitavaji: sekce NESPAROVANO a NEZARAZENO jsou pojistky proti
    prehlednuti nezmigrovane sluzby, respektive nezarazeneho objektu, a
    filtrovani se na ne nevztahuje, takze prepocet jejich cisel by tise
    smazal presne to, co maji sekce ukazat.

    Ze uz to neni cely beh, nese `filtered` - kdyby to vysledek nerekl,
    prepoctena cisla by byla jen druha podoba teze chyby.
    """
    if not text and not statuses:
        return result

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

    # Vazba L2+L3: partner (druha polovina paru) jede s vybranym scopem dal,
    # i kdyz sam kriteriu neodpovida - jinak by hlavicka odkazovala "blok
    # vyse/nize" na scope, ktery filtr uz smazal. Partner se pocita jen z
    # toho, co uz proslo textem i statusem, ne z puvodni mnoziny - jinak by
    # obe kriteria drzela pri sobe kazdou spojenou dvojici bez ohledu na to,
    # jestli aspon jedna strana skutecne sedi.
    kept_ids = {scope.scope_id for scope in scopes}
    if kept_ids:
        scopes = list(result.scopes)
        scopes = [
            scope
            for scope in scopes
            if scope.scope_id in kept_ids
            or (scope.link and scope.link["peer_scope_id"] in kept_ids)
        ]

    # L1 rodic jede s vybranou sluzbou dal - jinak by port zustal bez
    # sve hlavicky a "seskupeni po portech" by u profiltrovaneho vystupu
    # ukazovalo sluzbu viset bez rodice.
    kept_ids = {scope.scope_id for scope in scopes}
    if kept_ids:
        kept_ids |= _l1_parent_ids(result.scopes, kept_ids)
        scopes = [scope for scope in result.scopes if scope.scope_id in kept_ids]

    summary = {
        **result.summary,
        **count_statuses(check.status for scope in scopes for check in scope.checks),
    }
    # Zaklad je existujici marker z evaluate (service_types/profile) - CLI
    # filtr ho DOPLNUJE, ne prepisuje. scopes_shown/scopes_total od CLI
    # filtru prepsat smi (CLI vybira z uz profiltrovane mnoziny), ale
    # service_types/profile musi prezit, jinak by report tvaril, ze
    # engine filtr vubec nebezel.
    # Zaklad je existujici marker z evaluate (service_types/profile) - CLI
    # filtr ho DOPLNUJE, ne prepisuje. scopes_shown/scopes_total od CLI
    # filtru prepsat smi (CLI vybira z uz profiltrovane mnoziny), ale
    # service_types/profile musi prezit, jinak by report tvaril, ze
    # engine filtr vubec nebezel.
    applied: dict[str, object] = dict(result.filtered or {})
    applied.update(
        {
            "scopes_shown": len(scopes),
            "scopes_total": len(result.scopes),
        }
    )
    if text:
        applied["text"] = text
    if statuses:
        applied["statuses"] = sorted(status.value for status in statuses)

    return replace(result, scopes=scopes, summary=summary, filtered=applied)


def _section_header(section: Section) -> str:
    """Nadpis sekce bez doplneni na sirku bloku.

    Doplneni je zamerne az na volajicim: nadpis musi do sirky bloku vstoupit
    svou vlastni delkou drive, nez se sirka spocita. Kdyz se doplnoval NA
    sirku, ale do ni se nezapocitaval, prerostl u sluzby s vice rozsahy
    ramec o desitky znaku.
    """
    if section.family is None:
        return ""
    title = FAMILY_TITLE[section.family]
    addresses = ", ".join(section.addresses) or "-"
    gateway = f"   VGW {', '.join(section.virtual_gw)}" if section.virtual_gw else ""
    return f" -- {title}  {addresses}{gateway} "


def _group_header(group: Group) -> str:
    """Nadpis skupiny. Nedoplnuje se pomlckami na sirku bloku.

    Sekce rodiny caru pres celou sirku ma; skupina ne, aby zustaly obe
    urovne nadpisu rozlisitelne. Do SIRKY bloku ale nadpis vstupuje
    (AR-38) - jen se do ni nedoplnuje.
    """
    return f"   -- {group.title}"


def _block(view: ServiceView, has_baseline: bool, color: bool) -> list[str]:
    """Blok jedne sluzby. Sirky se pocitaji ze VSECH radku bloku.

    Kdyby si je pocitala kazda sekce zvlast, neseden by na spolecnou
    oddelovaci caru, ktera se kresli jednou. Neorezava se: orezana IPv6
    adresa nebo jmeno RIB jsou horsi nez nic.
    """
    rows = [row for section in view.sections for row in section.all_rows()]
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

    def line(status_cell: str, label: str, value: str, change: str) -> str:
        text = f" {status_cell} | {label:<{label_width}} : {value:<{value_width}}"
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
    header_rest = (
        f"  {view.description}   "
        f"{view.service_type}   {ports}   RI: {instance}"
    )
    # Pro vypocet sirky ramecku cisty text; obarvena varianta ma delsi
    # len() o escape sekvence a prerostla by "=" caru.
    header_line = f" {SYMBOL[view.status].strip():<4}{header_rest}"
    header_out = f" {_status_cell(view.status, 4, color=color)}{header_rest}"

    # Nadpisy sekci taky - nesou adresy rodiny, kterych muze byt vic, a u
    # ctyr rozsahu jsou delsi nez cela tabulka sloupcu. Treti vyskyt tehoz
    # tvaru: u hlavicky bloku i u souhrnne tabulky uz to ostre overeni
    # naslo, pokazde na skutecnych datech z laborky.
    headers = [_section_header(section) for section in view.sections]
    # Nadpisy skupin taky - ctvrty vyskyt tehoz tvaru, ktery komentar vys
    # popisuje u hlavicky bloku, souhrnne tabulky a nadpisu sekce. Tady
    # nesou jmeno peeru a RIB, coz u dlouheho jmena routing instance
    # prekona celou tabulku sloupcu.
    group_titles = [
        _group_header(group) for section in view.sections for group in section.groups
    ]
    note_line = f" {view.link_note}" if view.link_note else None

    width = max(
        [table_width, len(header_line)]
        + ([len(note_line)] if note_line else [])
        + [len(text) for text in headers]
        + [len(text) for text in group_titles]
    )

    lines = ["=" * width, header_out]
    if note_line:
        lines.append(note_line)
    lines.append("=" * width)
    lines.append(line(f"{'STAV':<4}", label_title, value_title, change_title))
    separator = f" {'-'*4}-+-{'-'*label_width}-+-{'-'*value_width}"
    if has_baseline:
        separator += f"-+-{'-'*change_width}"
    lines.append(separator)

    for section, header in zip(view.sections, headers):
        if header:
            lines.append("")
            lines.append(header + "-" * max(0, width - len(header)))
        for row in section.rows:
            lines.append(
                line(_status_cell(row.status, 4, color=color), row.label, row.value, changes[id(row)])
            )
        for group in section.groups:
            lines.append(_group_header(group))
            for row in group.rows:
                lines.append(
                    line(_status_cell(row.status, 4, color=color), row.label, row.value, changes[id(row)])
                )

    lines.append("")
    return lines


# Vzdy se ukazuji vsechny stavy - i kdyz je pocet nulovy, aby byl report
# konzistentni. INFO se vzhledem k tomu, ze je nove, prida na konec, aby
# starsi skript nectici posledni sloupec neparazil.
COUNT_NAMES = (("pass", "PASS"), ("warn", "WARN"), ("fail", "FAIL"), ("skip", "SKIP"), ("info", "INFO"))


def _counts_lines(services: dict[str, int], checks: dict[str, int], color: bool) -> list[str]:
    """Dva pojmenovane radky souhrnu misto jednoho neoznaceneho.

    Souhrn scital checky, ale tabulka hned pod nim ma radek na sluzbu -
    dve ruzne jednotky nad sebou a nikde neni receno ktera je ktera.
    Rozdelení podle AR-4 (jeden finding na fakt) ten rozdil jeste
    znasobilo: 83 PASS nad tabulkou o 11 radcich.

    Sirky se pocitaji z obou radku najednou, aby cisla stala pod sebou -
    jinak se dvojciferny pocet checku rozjede proti jednocifernemu poctu
    sluzeb a porovnat je oci nedokazou.
    """
    widths = {
        key: max(len(str(services[key])), len(str(checks[key]))) for key, _ in COUNT_NAMES
    }

    def line(title: str, counts: dict[str, int]) -> str:
        return f"  {title:<7} " + "  ".join(
            f"{counts[key]:>{widths[key]}} {_colorize(Status(name), name, color)}"
            for key, name in COUNT_NAMES
        )

    return [line("Sluzby:", services), line("Checky:", checks)]


def _filter_note(result: RunResult) -> list[str]:
    """Rekne nahlas, ze cisla pod tim uz nejsou za cely beh."""
    applied = result.filtered
    if not applied:
        return []

    criteria = []
    if applied.get("text"):
        criteria.append(f"text={applied['text']}")
    if applied.get("statuses"):
        criteria.append(f"status={','.join(applied['statuses'])}")

    return [
        f"  filtr: {'  '.join(criteria)} -- "
        f"{applied['scopes_shown']} z {applied['scopes_total']} sluzeb",
        "  (pocty sluzeb a checku plati za vyber; radek Sparovano ani sekce"
        " NESPAROVANO ci NEZARAZENO se neprepocitavaji)",
        "",
    ]


# Poradi je soucast pozadavku: sekce se cte shora dolu a BGP peer je
# nejcastejsi pripad.
UNASSIGNED_TITLES = (
    ("bgp_peers", "BGP peer"),
    ("static_routes", "Staticka routa"),
    ("bfd_sessions", "BFD session"),
)


def _unassigned_row(kind: str, item: dict[str, object]) -> tuple[str, str]:
    """Rozpad na identitu a podrobnost, ne jeden neprusvitny retezec.

    Kdyby to byl jeden retezec, tri druhy objektu by daly tri ruzne dlouhe
    identity a podrobnost by skoncila ve trech ruznych sloupcich - presne ta
    vada, kterou AR-40 opravuje o kus vys v NESPAROVANO.

    `via` se od `next_hop` odlisuje slovem, ne jen sipkou: `-> et-0/0/8.13`
    by vydavalo rozhrani za branu.
    """
    if kind == "bgp_peers":
        return item["peer"], f"RI {item.get('routing_instance') or '-'}"
    if kind == "static_routes":
        hops = item.get("next_hop") or []
        detail = (
            f"-> {', '.join(hops)}"
            if hops
            else f"via {', '.join(item.get('via') or ['-'])}"
        )
        return f"{item['rib']} {item['prefix']}", detail
    return item["peer"], f"{item.get('interface') or '-'}   {item.get('state') or '-'}"


def _unassigned_lines(result: RunResult) -> list[str]:
    """Objekty, ktere si nenarokovala zadna sluzba.

    Vypisuje se VZDY, i prazdna, a filtrovani se na ni nevztahuje - je to
    pojistka proti mezeram v parsovani (routa, kterou parser neumel precist,
    se do selektoru nedostane, ale v tabulce ji videt je). Pojistka, kterou
    je nutne si vyzadat prepinacem, chyti min. Totez odduvodneni nese
    docstring filter_result u NESPAROVANO.

    '(jen subject)' v nadpisu neni kosmetika: engine plni vsechny tri
    seznamy jen ze subjectu a v device scope vraci prazdno. Bez teto
    poznamky by prazdna sekce tvrdila 'nic nezarazeneho neni', zatimco se
    ve skutecnosti nesbiralo.
    """
    lines = ["NEZARAZENO (jen subject)"]
    rows = [
        (title, *_unassigned_row(kind, item))
        for kind, title in UNASSIGNED_TITLES
        for item in result.unassigned.get(kind, [])
    ]
    if not rows:
        lines.append("  (nic)")
        return lines
    # Obe sirky z obsahu, stejne jako v NESPAROVANO o kus vys (AR-5).
    title_width = max(len(title) for title, _, _ in rows)
    identity_width = max(len(identity) for _, identity, _ in rows)
    for title, identity, detail in rows:
        lines.append(f"  {title:<{title_width}}  {identity:<{identity_width}}  {detail}")
    return lines


def render(result: RunResult, *, detail: bool = False, color: bool = False) -> str:
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

    lines.extend(_filter_note(result))

    summary = result.summary
    # Sluzby se pocitaji tady, ne v engine: filtr uz scopy profiltroval,
    # takze stejny vypocet da spravne cislo v obou rezimech - za cely beh
    # i za vyber. Souhrn za checky prepocitava filtr sam, ten se ze scopu
    # odvodit neda bez toho, aby renderer zacal scitat checky.
    services = count_statuses(scope.status for scope in result.scopes)
    lines.extend(_counts_lines(services, summary, color))
    lines.append(
        f"  Sparovano {summary['scopes_matched']} sluzeb, "
        f"{summary['unmatched_baseline']} nesparovana v baseline, "
        f"{summary['unmatched_subject']} nesparovane v subject"
    )
    lines.append("")

    views = [(scope, build_view(scope, detail=detail)) for scope in result.scopes]

    # Sirky sloupcu se pocitaji z realnych dat, stejne jako v _block() -
    # napevno dane sirky prekypuji na skutecnych nazvech (dlouhy nazev
    # sluzby/RI z laborky), coz posune vsechny sloupce napravo a rozjede
    # zarovnani. Bez orezavani - orezany nazev je horsi nez nic.
    rows = [
        (
            view.status,
            view.description,
            view.service_type,
            view.baseline_interfaces[0] if view.baseline_interfaces else "-",
            view.subject_interfaces[0] if view.subject_interfaces else "-",
            view.routing_instance or "-",
            view.worst_message,
        )
        for _scope, view in views
    ]

    # STAV je uzavrena mnozina symbolu (4 znaky nebo prazdny) (viz SYMBOL) -
    # napevno dana sirka mu nikdy nemuze prerust, stejne jako sloupci STAV v _block().
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
            f"{_status_cell(status, status_w, color=color)} {description:<{service_w}} {service_type:<{type_w}} "
            f"{old_port:<{old_port_w}} {new_port:<{new_port_w}} {instance:<{ri_w}} "
            f"{message}".rstrip()
        )
    lines.append("")

    # Rozbaluje stav, ne interaktivita: --detail rozbali i PASS. Vazba L2+L3
    # drzi dvojici pohromade - kdyz se vypisuje jeden z bloku, vypise se i
    # jeho partner, jinak by odkaz "blok nize/vyse" ukazoval do prazdna.
    shown = {
        scope.scope_id
        for scope, view in views
        if detail or view.status is not Status.PASS
    }
    for scope, _view in views:
        link = scope.link
        if link and link["peer_scope_id"] in shown:
            shown.add(scope.scope_id)
    shown |= _l1_parent_ids([scope for scope, _view in views], shown)
    for scope, view in views:
        if scope.scope_id in shown:
            lines.extend(_block(view, has_baseline, color))

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
        # Sirka z obsahu, ne napevno - stejne pravidlo jako u popisku o radek
        # vys (AR-5). Zavorky se pocitaji do sirky, ne kolem ni: jinak by se
        # o dva znaky rozesly radky s ruzne dlouhym typem.
        type_width = max(len(service_type) for _, _, service_type, _ in rows) + 2
        for side, label, service_type, reason in rows:
            typed = f"({service_type})"
            lines.append(
                f"  {side:<9} {label:<{label_width}} {typed:<{type_width}}  {reason}"
            )

    lines.append("")
    lines.extend(_unassigned_lines(result))

    return "\n".join(lines) + "\n"
