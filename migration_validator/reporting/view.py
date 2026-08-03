"""Prevod vysledku na data reportu.

Oddelene od sazby zamerne: poradi sekci a zarazeni radku do rodin jde
testovat porovnanim datovych struktur, zatimco sirky sloupcu je nutne
testovat proti retezci s mezerami. V jednom souboru by se logika testovala
pres mezery.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from migration_validator.models.result import CheckResult, ScopeResult, Status

# Poradi sekci. None jsou radky, ktere na rodine nezavisi (stav rozhrani) -
# ty stoji hned pod hlavickou bloku a vlastni nadpis nemaji, protoze nadpis
# sekce nese adresu a tyhle radky zadnou nemaji.
FAMILY_ORDER = (None, 4, 6)

_STATUS_ORDER = (Status.FAIL, Status.WARN, Status.SKIP, Status.PASS)

NO_BASELINE = "bez baseline"


@dataclass
class Row:
    status: Status
    label: str
    value: str
    baseline_value: str | None
    delta: str | None
    mode: str


@dataclass
class Group:
    """Pojmenovana skupina radku uvnitr sekce rodiny.

    Nadpis skupinu OTEVIRA a nic ji nezavira - proto plati AR-37: radky bez
    skupiny stoji nahore, pred prvnim nadpisem. Kdyby stal radek bez
    skupiny za posledni skupinou, cetl by se jako jeji soucast.
    """

    title: str
    rows: list[Row] = field(default_factory=list)


@dataclass
class Section:
    family: int | None
    addresses: list[str] = field(default_factory=list)
    virtual_gw: list[str] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)
    groups: list[Group] = field(default_factory=list)

    def all_rows(self) -> list[Row]:
        """Vsechny radky sekce, neseskupene i ve skupinach.

        Sirky sloupcu se pocitaji odsud. Kdyby vracela jen `rows`, dlouha
        hodnota uvnitr skupiny by prerostla ramec bloku.
        """
        return [*self.rows, *(row for group in self.groups for row in group.rows)]


@dataclass
class ServiceView:
    status: Status
    description: str
    service_type: str
    routing_instance: str | None
    baseline_interfaces: list[str] = field(default_factory=list)
    subject_interfaces: list[str] = field(default_factory=list)
    worst_message: str = ""
    sections: list[Section] = field(default_factory=list)


def change_text(row: Row, has_baseline: bool) -> str:
    """Obsah sloupce ZMENA.

    Rozliseni podle rezimu checku je nutne, ne kosmeticke: STATE checky
    (arp_present, ping_reachability, interface_state) baseline hodnotu
    nemaji z definice, takze bez tohoto by se 'bez baseline' vytisklo na
    vetsine radku a hlasku by nikdo necetl.
    """
    if not has_baseline:
        return ""
    if row.mode == "state":
        return ""
    if row.baseline_value is None:
        # SKIP uz duvod nese ve vlastni hlasce ("peer neni v baseline
        # snapshotu"), takze 'bez baseline' vedle ni je druha kopie teze
        # vety - v ostrem behu 14 z 18 vyskytu te hlasky. Podminka je uzka
        # zamerne: SKIP se znamou drivejsi hodnotou ji ma dal vypsat.
        return "" if row.status is Status.SKIP else NO_BASELINE
    if row.baseline_value == row.value:
        return ""
    if row.delta:
        return f"bylo {row.baseline_value}   {row.delta}"
    return f"bylo {row.baseline_value}"


def _row(check: CheckResult, qualify: bool) -> Row:
    """Radek reportu.

    `qualify` je True, kdyz ma rodina vic nez jednu adresu - pak radky
    vazane na konkretni adresu (ARP, ND, ping) nesou tuto adresu v popisku.
    U jedine adresy se vypousti, protoze uz je v hlavicce sekce.
    """
    label = check.label or check.id
    address = check.details.get("address")
    if qualify and address:
        label = f"{label} ({address})"

    return Row(
        status=check.status,
        label=label,
        # Pomlcka, ne `check.message`: sloupec je podle AR-4 hodnota, ne
        # veta. Kdyz sem message padala, delsi hlaska (RPC chyba od
        # collectoru) roztahla cely blok na 270 znaku sirky. Hodnotu dodava
        # check, veta patri do sloupce NALEZ a do strojoveho vystupu;
        # tenhle fallback uz jen kryje check, ktery na ni zapomene.
        value=check.value if check.value is not None else "-",
        baseline_value=check.baseline_value,
        delta=check.delta,
        mode=check.mode,
    )


def _worst_message(scope: ScopeResult) -> str:
    if scope.status is Status.PASS:
        return ""
    for status in _STATUS_ORDER:
        if status is Status.PASS:
            continue
        for check in scope.checks:
            if check.status is status:
                return check.message
    return ""


def build_view(scope: ScopeResult) -> ServiceView:
    """Slozi z vysledku sluzby vse, co report vypisuje.

    Sekce prazdne rodiny se nevytvari - sluzba bez IPv6 nema mit prazdnou
    IPv6 sekci.
    """
    identity = scope.identity or {}
    addresses = {4: list(identity.get("ipv4", [])), 6: list(identity.get("ipv6", []))}
    gateways = {
        4: list(identity.get("virtual_gw_v4", [])),
        6: list(identity.get("virtual_gw_v6", [])),
    }

    sections: list[Section] = []
    for family in FAMILY_ORDER:
        checks = [check for check in scope.checks if check.family == family]
        if not checks:
            continue
        own = addresses.get(family, [])
        qualify = len(own) > 1
        rows: list[Row] = []
        groups: list[Group] = []
        by_title: dict[str, Group] = {}
        for check in checks:
            row = _row(check, qualify=qualify)
            if check.group is None:
                rows.append(row)
                continue
            group = by_title.get(check.group)
            if group is None:
                group = Group(title=check.group)
                by_title[check.group] = group
                groups.append(group)
            group.rows.append(row)
        sections.append(
            Section(
                family=family,
                addresses=own,
                virtual_gw=gateways.get(family, []),
                rows=rows,
                groups=groups,
            )
        )

    match = scope.match
    return ServiceView(
        status=scope.status,
        description=identity.get("description") or scope.scope_id,
        service_type=identity.get("service_type") or "-",
        routing_instance=identity.get("routing_instance"),
        baseline_interfaces=list(match.baseline_interfaces) if match else [],
        # Bez match (beh bez baseline, nesparovana sluzba) porty nese identity.
        # Baseline strana zustava prazdna - zadna neexistuje.
        subject_interfaces=(
            list(match.subject_interfaces) if match else list(identity.get("interfaces", []))
        ),
        worst_message=_worst_message(scope),
        sections=sections,
    )
