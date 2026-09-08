"""EVPN checky pro E-Line (vpws) a E-LAN (vlan-aware, vlan-based).

Platformni rozdil MX (virtual-switch/bridge-domain) vs EVO (mac-vrf/VLAN)
resi collector - sem uz prichazi jednotne schema, takze tady neni zadna
vetev na platformu.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from migration_validator.checks.base import Check, CheckContext, Mode
from migration_validator.checks.baseline import UNKNOWN, suffix, unchanged_or
from migration_validator.checks.ifaces import percent_change, qualified
from migration_validator.checks.registry import register
from migration_validator.models.result import Finding, Outcome, Severity

UP = "Up"


def _is_up(status: str) -> bool:
    """Junos hlasi stav i s doplnkem za lomitkem.

    Lokalni rozhrani v ESI je 'Up/Forwarding', VPWS rozhrani jen 'Up'.
    Porovnani na presnou rovnost by to prvni oznacilo za rozbite.
    """
    return status.split("/", 1)[0].strip() == UP


def _find_baseline_peer(
    baseline_peers: list[dict[str, Any]], ipaddr: Any
) -> dict[str, Any] | None:
    """Najde baseline peera podle ipaddr v jiz pozicne sparovanem SID.

    Jmena rozhrani se migraci meni, ale IP adresa vzdaleneho PE ne - proto
    je ipaddr jedine spolehlive kriterium pro parovani peeru uvnitr SID.
    """
    for peer in baseline_peers:
        if peer.get("ipaddr") == ipaddr:
            return peer
    return None


@register
class EvpnVpwsStatusCheck(Check):
    """Stav EVPN-VPWS per SID a peer.

    Stav se ted cte vyhradne z 'evpn-vpws-sid-pe-status' - drivejsi
    porovnani jen SID cisel a stavu rozhrani tuhle tabulku vubec necetlo.
    Mode.BOTH je opravneny: baseline_value se dopocitava pozicnim
    parovanim rozhrani a peeru podle ipaddr (viz _find_baseline_peer),
    takze sloupec ZMENA nese skutecny rozdil, ne trvale "bez baseline".
    Drivejsi radek 'chybi remote SID' nahrazuji dva BROKEN radky remote
    PE / remote status.
    """

    id = "evpn_vpws_status"
    title = "Stav EVPN-VPWS"
    label = "EVPN VPWS status"
    mode = Mode.BOTH
    requires = ("evpn_vpws",)
    service_types = frozenset({"E-Line"})
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        instances: dict[str, Any] = ctx.subject.get("evpn_vpws", {})
        if not instances:
            return [
                Finding(Outcome.SKIP, "pro tento scope nejsou data evpn-vpws", value="bez dat")
            ]

        # Jmeno instance (RI) migraci prezije, takze parovani baseline
        # instance je proste podle jmena - na rozdil od rozhrani uvnitr ni.
        baseline_instances: dict[str, Any] = (ctx.baseline or {}).get("evpn_vpws", {})

        findings: list[Finding] = []
        for name in sorted(instances):
            interfaces = instances[name].get("interfaces", [])
            baseline_interfaces = baseline_instances.get(name, {}).get("interfaces", [])
            many = len(interfaces) > 1
            for idx, iface in enumerate(interfaces):
                # Pozicni parovani rozhrani: jmena rozhrani se migraci meni
                # (ge-0/0/3.0 -> ae0.224), takze jmeno pro parovani s
                # baseline pouzit nejde - stejny princip jako pozicni zip
                # v _aligned_baseline_data (engine.py).
                baseline_iface = (
                    baseline_interfaces[idx] if idx < len(baseline_interfaces) else None
                )
                findings.extend(
                    self._interface_findings(name, iface, baseline_iface, many, ctx)
                )
        return findings

    def _interface_findings(
        self,
        instance: str,
        iface: dict[str, Any],
        baseline_iface: dict[str, Any] | None,
        qualify: bool,
        ctx: CheckContext,
    ) -> list[Finding]:
        def label(text: str) -> str:
            return qualified(text, iface["name"]) if qualify else text

        findings = []
        status = str(iface.get("status", "unknown"))
        baseline_status = (
            str(baseline_iface.get("status", "unknown"))
            if baseline_iface is not None
            else None
        )
        up = _is_up(status)
        outcome = (
            Outcome.OK if up
            else unchanged_or(
                Outcome.BROKEN, ctx, "evpn_vpws",
                same=status != UNKNOWN and baseline_status == status,
            )
        )
        findings.append(
            Finding(
                outcome,
                f"{instance}: stav rozhrani {status}"
                + ("" if up else f", ocekavano {UP}")
                + suffix(outcome),
                label=label("EVPN VPWS local interface status"),
                value=status,
                baseline_value=baseline_status,
                subject={"interface": iface["name"], "status": status},
            )
        )
        findings.extend(self._sid_findings(instance, iface, baseline_iface, "local", label, ctx))
        findings.extend(self._sid_findings(instance, iface, baseline_iface, "remote", label, ctx))
        return findings

    def _sid_findings(
        self,
        instance: str,
        iface: dict[str, Any],
        baseline_iface: dict[str, Any] | None,
        side: str,
        label,
        ctx: CheckContext,
    ) -> list[Finding]:
        sid = iface.get(f"{side}_sid") or {"value": None, "peers": []}
        value = sid.get("value")
        peers = sid.get("peers") or []
        prefix = f"EVPN VPWS SID {side}"

        baseline_sid = (
            baseline_iface.get(f"{side}_sid") if baseline_iface is not None else None
        )
        baseline_peers = (baseline_sid.get("peers") or []) if baseline_sid else []
        baseline_sid_value = (
            f"SID {baseline_sid.get('value') if baseline_sid.get('value') is not None else '?'}"
            if baseline_sid is not None
            else None
        )

        findings = [
            Finding(
                Outcome.INFO,
                f"{instance}: {side} SID {value if value is not None else '?'}",
                label=label(f"{prefix} value"),
                value=f"SID {value if value is not None else '?'}",
                baseline_value=baseline_sid_value,
            )
        ]

        if not peers:
            if side == "remote":
                # Remote peer musi existovat vzdy - jeho absence znamena
                # nenakonfigurovanou nebo spadlou druhou stranu. Kdyz
                # baseline peer mela, "bylo Resolved" je presne informace,
                # kterou operator potrebuje - proto se pujcuje z prvniho
                # baseline peeru, i kdyz v subjektu zadny peer neni.
                first_baseline_peer = baseline_peers[0] if baseline_peers else None
                same = baseline_iface is not None and not baseline_peers
                outcome = unchanged_or(Outcome.BROKEN, ctx, "evpn_vpws", same=same)
                findings.append(
                    Finding(
                        outcome,
                        f"{instance}: remote peer chybi" + suffix(outcome),
                        label=label(f"{prefix} PE"),
                        value="Neznamy peer",
                        baseline_value=(
                            "Neznamy peer" if same
                            else (
                                str(first_baseline_peer.get("ipaddr") or "?")
                                if first_baseline_peer
                                else None
                            )
                        ),
                    )
                )
                findings.append(
                    Finding(
                        outcome,
                        f"{instance}: remote SID nema zadny Resolved zaznam" + suffix(outcome),
                        label=label(f"{prefix} status"),
                        value="Unresolved / Chybi",
                        baseline_value=(
                            "Unresolved / Chybi" if same
                            else (
                                str(first_baseline_peer.get("status") or "Unresolved / Chybi")
                                if first_baseline_peer
                                else None
                            )
                        ),
                    )
                )
            else:
                # Local peery nese jen multihoming - u single-homed jde
                # o ocekavany stav, ne o vadu. Baseline lze pouzit jen
                # kdyz ma stejny tvar radku (taky bez local peeru) -
                # kdyz baseline peery MELA, jde o jiny tvar hlasky a
                # srovnani by nedavalo smysl, baseline_value zustava None.
                subject_mode = iface.get("mode") or "unknown"
                value = f"{subject_mode} (multi-homing peer ve vypisu nenalezen)"
                baseline_local_mode = None
                if (
                    baseline_iface is not None
                    and baseline_sid is not None
                    and not baseline_peers
                ):
                    baseline_mode = baseline_iface.get("mode") or "unknown"
                    # Pri shode modu jde do baseline_value cela hodnota
                    # radku, aby change_text poznal rovnost a nechal ZMENU
                    # prazdnou. Pri rozdilu jde jen cisty mod - zavorka o
                    # nenalezenem peeru popisuje subjekt, ne baseline, a
                    # "bylo single-homed (multi-homing peer...)" by tvrdila
                    # o baseline vic, nez check vi.
                    baseline_local_mode = (
                        value if baseline_mode == subject_mode else baseline_mode
                    )
                findings.append(
                    Finding(
                        Outcome.INFO,
                        f"{instance}: local strana bez multi-homing peeru",
                        label=label(f"{prefix} mode"),
                        value=value,
                        baseline_value=baseline_local_mode,
                    )
                )
            return findings

        peer_label = f"{prefix} peer PE" if side == "local" else f"{prefix} PE"
        for peer in peers:
            baseline_peer = _find_baseline_peer(baseline_peers, peer.get("ipaddr"))
            resolved = (peer.get("status") or "").strip().lower() == "resolved"
            was_resolved = (
                baseline_peer is not None
                and (baseline_peer.get("status") or "").strip().lower() == "resolved"
            )
            outcome = (
                Outcome.OK if resolved
                else unchanged_or(
                    Outcome.BROKEN, ctx, "evpn_vpws",
                    same=baseline_peer is not None and not was_resolved,
                )
            )
            reason = (
                ""
                if resolved
                else f" neni Resolved ({peer.get('status') or 'chybi'})"
            )
            findings.append(
                Finding(
                    outcome,
                    f"{instance}: {side} peer {peer.get('ipaddr')}{reason}" + suffix(outcome),
                    label=label(peer_label),
                    value=str(peer.get("ipaddr") or "?"),
                    baseline_value=(
                        str(baseline_peer.get("ipaddr") or "?") if baseline_peer else None
                    ),
                    subject=dict(peer),
                )
            )
            findings.append(
                Finding(
                    outcome,
                    f"{instance}: {side} peer {peer.get('ipaddr')} "
                    f"status {peer.get('status') or 'chybi'}" + suffix(outcome),
                    label=label(f"{prefix} status"),
                    value=str(peer.get("status") or "Unresolved / Chybi"),
                    baseline_value=(
                        str(baseline_peer.get("status") or "Unresolved / Chybi")
                        if baseline_peer
                        else None
                    ),
                )
            )
            for info_label, key in (("mode", "mode"), ("ESI", "esi"), ("role", "role")):
                if peer.get(key):
                    findings.append(
                        Finding(
                            Outcome.INFO,
                            f"{instance}: {side} peer {info_label} {peer[key]}",
                            label=label(f"{prefix} {info_label}"),
                            value=str(peer[key]),
                            baseline_value=(
                                str(baseline_peer[key])
                                if baseline_peer and baseline_peer.get(key)
                                else None
                            ),
                        )
                    )
        return findings


@register
class EvpnEsiStatusCheck(Check):
    """Blok radku per ESI misto jednoho slepeneho radku.

    Puvodni jediny radek 'Up/Forwarding  DF 150.0.0.12' s ESI v labelu se
    spatne cetl a u nezvoleneho DF vypsal 'DF DF not elected yet' (Junos
    dava do esi-designated-forwarder literal 'DF not elected yet'). Novy
    tvar: INFO hlavicka s ESI, pak ESI Status (popisny 'Resolved by IFL
    ...', na rovnost s baseline se neporovnava - nese jmeno IFL, ktere se
    migraci meni), ESI Local interface status a ESI DF, kazdy s vlastnim
    verdiktem. Radek ESI Status se u snapshotu bez resolved_status
    vynechava - stav se nefabuluje.
    """

    id = "evpn_esi_status"
    title = "Stav EVPN ESI"
    label = "EVPN ESI status"
    mode = Mode.BOTH
    requires = ("evpn_esi",)
    service_types = frozenset({"E-LAN"})
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        entries: dict[str, Any] = ctx.subject.get("evpn_esi", {})
        if not entries:
            return [
                Finding(Outcome.SKIP, "pro tento scope nejsou data evpn-esi", value="bez dat")
            ]

        baseline_entries = (ctx.baseline or {}).get("evpn_esi", {})

        findings: list[Finding] = []
        for esi in sorted(entries):
            findings.extend(
                self._esi_block(esi, entries[esi], baseline_entries.get(esi), ctx)
            )
        return findings

    def _esi_block(
        self,
        esi: str,
        data: dict[str, Any],
        baseline: dict[str, Any] | None,
        ctx: CheckContext,
    ) -> list[Finding]:
        # ESI je HODNOTA hlavicky, ne label: kazdy radek musi mit hodnotu
        # (invariant end-to-end testu) a v labelu by dlouhe ESI roztahlo
        # sloupec CHECK celeho bloku. Baseline hodnota je shodne ESI, kdyz
        # zaznam v baseline je - jinak by hlavicka hlasila "bez baseline"
        # i u sparovaneho segmentu.
        findings = [
            Finding(
                Outcome.INFO,
                f"ESI {esi}",
                label="ESI",
                value=esi,
                baseline_value=esi if baseline else None,
            )
        ]
        baseline = baseline or {}

        resolved = data.get("resolved_status")
        if resolved:
            resolved_ok = resolved.lower().startswith("resolved")
            baseline_resolved = baseline.get("resolved_status")
            same = (
                baseline_resolved is not None
                and not baseline_resolved.lower().startswith("resolved")
            )
            outcome = (
                Outcome.OK if resolved_ok
                else unchanged_or(Outcome.BROKEN, ctx, "evpn_esi", same=same)
            )
            findings.append(
                Finding(
                    outcome,
                    f"{esi}: {resolved}" + suffix(outcome),
                    label="ESI Status",
                    value=resolved,
                    baseline_value=baseline_resolved,
                )
            )

        # Hodnota nese jen stav, ne jmeno IFL - to se migraci meni, coby
        # cast hodnoty by shodny stav pred a po migraci vypadal jako
        # zmena (R-5). Jmeno zustava jen v subject (pro report).
        status = str(data.get("status", UNKNOWN))
        up = _is_up(status)
        baseline_status = baseline.get("status")
        same = (
            status != UNKNOWN
            and bool(baseline_status)
            and str(baseline_status) != UNKNOWN
            and not _is_up(str(baseline_status))
        )
        outcome = (
            Outcome.OK if up
            else unchanged_or(Outcome.BROKEN, ctx, "evpn_esi", same=same)
        )
        findings.append(
            Finding(
                outcome,
                f"{esi}: stav rozhrani {status}"
                + ("" if up else f", ocekavano {UP}")
                + suffix(outcome),
                label="ESI Local interface status",
                value=status,
                baseline_value=str(baseline_status) if baseline_status else None,
                subject={"status": status, "interface": data.get("interface")},
            )
        )

        # "" se chova jako chybejici hodnota (stejny stav, jiny zdroj dat).
        df = data.get("df_role") or None
        baseline_df = baseline.get("df_role")
        if df is None:
            # DF blok ve vypisu chybi - zadny verdikt, stav se nefabuluje.
            df_outcome = Outcome.INFO
            df_message = f"{esi}: DF bez zaznamu"
        else:
            not_elected = "not elected" in df.lower()
            same = "not elected" in str(baseline_df or "").lower()
            df_outcome = (
                Outcome.OK if not not_elected
                else unchanged_or(Outcome.BROKEN, ctx, "evpn_esi", same=same)
            )
            # Junos uz sam vraci "DF not elected yet" - kdyz text s "DF" uz
            # zacina, dalsi "DF " by hlaseni zdvojilo ("DF DF not elected...").
            text = df if df.upper().startswith("DF") else f"DF {df}"
            df_message = f"{esi}: {text}" + suffix(df_outcome)
        findings.append(
            Finding(
                df_outcome,
                df_message,
                label="ESI DF",
                value=df or "-",
                baseline_value=baseline_df or ("-" if baseline else None),
                subject={"df_role": df},
            )
        )
        return findings


def _count_finding(
    label: str,
    message: str,
    value: str,
    baseline_value: str | None,
    *,
    ok: bool,
    expectation: str,
    ctx: CheckContext,
    warn_below_baseline: bool = False,
    same_broken: bool = False,
) -> Finding:
    """Ciselny radek: stavove pravidlo; rozdil proti baseline nese ZMENA.

    Jedinym volajicim je EVPN neighbors (stavove pravidlo > 0). Rovnost s
    baseline se nevynucuje (revize spec 2.4 po overeni v laborce
    2026-08-06) - misto toho warn_below_baseline: pokles pod baseline je
    DEGRADED, ztraceny peer stoji za pozornost, ale u ciste L2 vlan-aware
    sluzby po migraci legitimne ubyde puvodni box, takze to neni tvrdy
    FAIL. Agregaty local/IRB interfacu, ktere driv rovnez pouzivaly tuhle
    funkci, Task 2 zrusil - jejich pocet se migraci meni pri kazde
    konsolidaci sluzeb do jedne mac-vrf instance a rovnost by FAILovala
    trvale, viz _ServiceUnits vyse.
    """
    outcome = (
        Outcome.OK if ok
        else unchanged_or(Outcome.BROKEN, ctx, "evpn_instance", same=same_broken)
    )
    text = (
        f"{message}: {value}" if ok
        else f"{message}: {value}, ocekavano {expectation}" + suffix(outcome)
    )
    if (
        ok
        and warn_below_baseline
        and baseline_value is not None
        and int(value) < int(baseline_value)
    ):
        outcome = Outcome.DEGRADED
        text = f"{message}: {value}, baseline {baseline_value}"
    return Finding(
        outcome, text, label=label, value=value, baseline_value=baseline_value
    )


@dataclass(frozen=True)
class _ServiceUnits:
    """Identita sluzby pro relevance filtr vlan-aware bloku.

    active=False vypina filtrovani - scope bez interface selektoru nema
    podle ceho vybirat a radsi vypise vsechno nez nic.
    """

    interfaces: frozenset[str]
    irb: str | None
    vlans: frozenset[str]

    @property
    def active(self) -> bool:
        return bool(self.interfaces)


def _service_units(ctx: CheckContext) -> _ServiceUnits:
    interfaces = frozenset(ctx.scope.selectors.interfaces)
    irb = None
    if ctx.link and ctx.link.get("role") == "l2":
        irb = ctx.link.get("peer_interface")
    vlans = frozenset(ctx.scope.selectors.vlans)
    if not vlans:
        # sluzba bez customer_vlan - unit cislo je stejna informace
        vlans = frozenset(
            name.split(".", 1)[1] for name in interfaces if "." in name
        )
    return _ServiceUnits(interfaces=interfaces, irb=irb, vlans=vlans)


@register
class EvpnInstanceStatusCheck(Check):
    """Per-instance zdravi EVPN podle brief pravidel ze zadani.

    RI-wide agregaty local/IRB interfacu (Task 2, spec 2026-08-12) jsou
    zrusene - pocet interfacu v instanci se migraci konsolidace sluzeb do
    jedne mac-vrf meni pri kazde migraci a byl by trvale nepouzitelny pro
    porovnani s baseline. Misto nich se posuzuji jen radky vlastnich unitu
    sluzby: "EVPN interface" (filtr na ctx.scope.selectors.interfaces) a
    "IRB interface" (jen kdyz je unit linkovany pres ctx.link, role "l2") -
    viz _service_units. Kdyz vlastni (nebo linkovany IRB) unit v instanci
    vubec neni - IFL se do mac-vrf nedostal, realny selhany stav migrace -
    misto ticha se emituje BROKEN radek "{unit} chybi v instanci" (dodatek
    specu 2026-08-12, review Tasku 2); u vice instanci ve scope (qualify)
    se tenhle radek preskakuje, protoze bez vedeni "ktera instance je ta
    spravna" by naivni per-instance kontrola falesne broken-ovala unit v
    kazde jine instanci. Realne service scopy (scoping/builder.py) maji
    vzdy presne jednu routing_instance, takze qualify=True u service
    scopu dnes nenastava - pojistka je precautionary, ne znama mezera.
    EVPN neighbors pod baseline jsou DEGRADED (viz _count_finding). Text
    ESI statusu se na rovnost neporovnava, nese jmeno IFL.
    """

    id = "evpn_instance_status"
    title = "Stav EVPN instance"
    label = "EVPN instance"
    mode = Mode.BOTH
    requires = ("evpn_instance",)
    service_types = frozenset({"E-LAN"})
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        instances: dict[str, Any] = ctx.subject.get("evpn_instance", {})
        if not instances:
            return [
                Finding(
                    Outcome.SKIP,
                    "pro tento scope nejsou data evpn-instance",
                    value="bez dat",
                )
            ]

        baseline_instances = (ctx.baseline or {}).get("evpn_instance", {})
        many = len(instances) > 1
        units = _service_units(ctx)

        findings: list[Finding] = []
        for name in sorted(instances):
            findings.extend(
                self._instance_findings(
                    name, instances[name], baseline_instances.get(name), many, units, ctx
                )
            )
        return findings

    def _instance_findings(
        self,
        instance: str,
        data: dict[str, Any],
        baseline: dict[str, Any] | None,
        qualify: bool,
        units: _ServiceUnits,
        ctx: CheckContext,
    ) -> list[Finding]:
        def label(text: str) -> str:
            return qualified(text, instance) if qualify else text

        baseline = baseline or {}
        findings: list[Finding] = []

        neighbors = data.get("neighbors", {})
        # Prazdny dict znamena "baseline zmerila nulu sousedu", ne "baseline
        # neni k dispozici" - proto se tu na rozdil od drive nesmi "or None"
        # koercit na None (viz baseline_value nize).
        baseline_neighbors = baseline.get("neighbors")
        findings.append(
            _count_finding(
                label("EVPN neighbors"),
                f"{instance}: EVPN neighbors",
                str(neighbors.get("total") or 0),
                (
                    str(baseline_neighbors.get("total") or 0)
                    if baseline_neighbors is not None
                    else None
                ),
                ok=(neighbors.get("total") or 0) > 0,
                expectation="> 0",
                warn_below_baseline=True,
                same_broken=(
                    baseline_neighbors is not None
                    and int(baseline_neighbors.get("total") or 0) == 0
                ),
                ctx=ctx,
            )
        )

        baseline_neighbor_addresses = set((baseline.get("neighbors") or {}).get("addresses", []))
        for address in neighbors.get("addresses", []):
            findings.append(
                Finding(
                    Outcome.INFO,
                    f"{instance}: neighbor {address}",
                    label=label("EVPN neighbor"),
                    value=address,
                    baseline_value=address if address in baseline_neighbor_addresses else None,
                )
            )

        findings.extend(self._esi_findings(instance, data, baseline, label, units, ctx))

        local = data.get("local_interfaces", {})
        local_entries = local.get("entries", [])
        local_names = {entry["name"] for entry in local_entries}
        baseline_local_by_name = {
            entry["name"]: entry
            for entry in baseline.get("local_interfaces", {}).get("entries", [])
        }
        for entry in local_entries:
            if units.active and entry["name"] not in units.interfaces:
                continue
            status = str(entry["status"])
            up = _is_up(status)
            baseline_entry = baseline_local_by_name.get(entry["name"])
            same = (
                status != UNKNOWN
                and baseline_entry is not None
                and str(baseline_entry["status"]) != UNKNOWN
                and not _is_up(str(baseline_entry["status"]))
            )
            outcome = (
                Outcome.OK if up
                else unchanged_or(Outcome.BROKEN, ctx, "evpn_instance", same=same)
            )
            findings.append(
                Finding(
                    outcome,
                    f"{instance}: interface {entry['name']} {status}"
                    + ("" if up else f", ocekavano {UP}")
                    + suffix(outcome),
                    # Jmeno IFL jde do labelu, hodnota nese jen stav - jinak
                    # by shodny stav pred a po migraci (jine jmeno) vypadal
                    # jako zmena (R-5).
                    label=label(f"EVPN interface ({entry['name']})"),
                    value=status,
                    baseline_value=(
                        baseline_entry["status"] if baseline_entry else None
                    ),
                )
            )
        # IFL, ktery se do mac-vrf teto instance vubec nedostal, by jinak
        # zustal neviditelny - blok by pro sluzbu nevypsal zadny EVPN
        # interface radek (dodatek specu 2026-08-12, review Tasku 2).
        # Multi-instance scope: unit patri tomu RI, ktere ho jmenuje, takze
        # se pri vice instancich (qualify=True) chybejici-unit radek
        # neemituje - jinak by naivni per-instance kontrola falesne
        # nahlasila unit jako chybejici v kazde instanci krome te spravne.
        if units.active and not qualify:
            for unit in sorted(units.interfaces - local_names):
                same = unit not in baseline_local_by_name and bool(baseline)
                outcome = unchanged_or(Outcome.BROKEN, ctx, "evpn_instance", same=same)
                b = baseline_local_by_name.get(unit)
                baseline_value = (
                    f"{unit} chybi v instanci" if outcome is Outcome.UNCHANGED
                    else (b["status"] if b else None)
                )
                findings.append(
                    Finding(
                        outcome,
                        f"{instance}: unit {unit} chybi v instanci" + suffix(outcome),
                        label=label(f"EVPN interface ({unit})"),
                        value=f"{unit} chybi v instanci",
                        baseline_value=baseline_value,
                    )
                )

        irb = data.get("irb_interfaces", {})
        irb_entries = irb.get("entries", [])
        irb_names = {entry["name"] for entry in irb_entries}
        baseline_irb_by_name = {
            entry["name"]: entry
            for entry in baseline.get("irb_interfaces", {}).get("entries", [])
        }
        for entry in irb_entries:
            if units.active and entry["name"] != units.irb:
                continue
            context = entry.get("l3_context")
            status = str(entry["status"])
            value = status
            if context:
                value += f" ({context})"
            up = _is_up(status)
            baseline_entry = baseline_irb_by_name.get(entry["name"])
            same = (
                status != UNKNOWN
                and baseline_entry is not None
                and str(baseline_entry["status"]) != UNKNOWN
                and not _is_up(str(baseline_entry["status"]))
            )
            outcome = (
                Outcome.OK if up
                else unchanged_or(Outcome.BROKEN, ctx, "evpn_instance", same=same)
            )
            baseline_value = None
            if baseline_entry:
                baseline_value = baseline_entry["status"]
                baseline_context = baseline_entry.get("l3_context")
                if baseline_context:
                    baseline_value += f" ({baseline_context})"
            findings.append(
                Finding(
                    outcome,
                    f"{instance}: IRB {entry['name']} {status}"
                    + ("" if up else f", ocekavano {UP}")
                    + suffix(outcome),
                    label=label(f"IRB interface ({entry['name']})"),
                    value=value,
                    baseline_value=baseline_value,
                )
            )
        if units.active and not qualify and units.irb and units.irb not in irb_names:
            same = units.irb not in baseline_irb_by_name and bool(baseline)
            outcome = unchanged_or(Outcome.BROKEN, ctx, "evpn_instance", same=same)
            b = baseline_irb_by_name.get(units.irb)
            baseline_value = (
                f"{units.irb} chybi v instanci" if outcome is Outcome.UNCHANGED
                else (b["status"] if b else None)
            )
            findings.append(
                Finding(
                    outcome,
                    f"{instance}: IRB unit {units.irb} chybi v instanci" + suffix(outcome),
                    label=label(f"IRB interface ({units.irb})"),
                    value=f"{units.irb} chybi v instanci",
                    baseline_value=baseline_value,
                )
            )
        return findings

    def _esi_findings(
        self,
        instance: str,
        data: dict[str, Any],
        baseline: dict[str, Any],
        label,
        units: _ServiceUnits,
        ctx: CheckContext,
    ) -> list[Finding]:
        if units.active:
            # Vlastni ESI sluzby nese blok checku evpn_esi_status (ESI
            # Status / Local interface status / DF) - drivejsi relevance
            # filtr (jen ESI jmenujici vlastni IFL) tu nechaval jediny
            # radek, ktery ten blok doslova opakoval (revize 2026-08-13).
            # Vetev "v baseline bylo, ted chybi" byla pri aktivnim filtru
            # stejne mrtva: chybejici ESI nema status text a bez nej filtr
            # nikdy nepustil (zamer, viz spec kap. 2 - baseline IFL nese
            # stare jmeno rozhrani). Bez selektoru zustava plny instancni
            # kontext vcetne SKIP a "chybi" radku.
            return []

        esis: dict[str, str] = data.get("esis", {})
        baseline_esis: dict[str, str] = baseline.get("esis", {}) if baseline else {}

        if not esis and not baseline_esis:
            # Single-homed instance zadne konfigurovane ESI nema - bez dat
            # je vysledek SKIP, ne chyba (spec 2.2).
            return [
                Finding(
                    Outcome.SKIP,
                    f"{instance}: zadne ESI ve vypisu",
                    label=label("ESI status"),
                    value="bez dat",
                )
            ]

        findings = []
        for esi in sorted(set(esis) | set(baseline_esis)):
            status = esis.get(esi)
            baseline_status = baseline_esis.get(esi)
            if status is None:
                findings.append(
                    Finding(
                        Outcome.BROKEN,
                        f"{instance}: ESI {esi} v baseline bylo, ted chybi",
                        label=label(f"ESI {esi}"),
                        value="chybi",
                        baseline_value=baseline_status,
                    )
                )
                continue
            # Substring by chytl i "Unresolved" - stejny duvod, proc VPWS
            # check (radek vyse) porovnava cele slovo, ne podretezec.
            resolved = status.lower().startswith("resolved")
            same = (
                baseline_status is not None
                and not baseline_status.lower().startswith("resolved")
            )
            outcome = (
                Outcome.OK if resolved
                else unchanged_or(Outcome.BROKEN, ctx, "evpn_instance", same=same)
            )
            findings.append(
                Finding(
                    outcome,
                    f"{instance}: ESI {esi} {status or 'bez statusu'}" + suffix(outcome),
                    label=label(f"ESI {esi}"),
                    value=status or "bez statusu",
                    # Text nese jmeno IFL, ktere se migraci meni - baseline
                    # se ukazuje, ale na rovnost se neporovnava.
                    baseline_value=baseline_status,
                )
            )
        return findings


@register
class EvpnMacCountCheck(Check):
    id = "evpn_mac_count"
    title = "Pocet MAC adres"
    label = "EVPN MAC count"
    mode = Mode.BOTH
    requires = ("evpn_mac",)
    service_types = frozenset({"E-LAN"})
    default_severity = Severity.ADVISORY

    def run(self, ctx: CheckContext) -> list[Finding]:
        instances: dict[str, Any] = ctx.subject.get("evpn_mac", {})
        if not instances:
            return [
                Finding(
                    Outcome.SKIP, "pro tento scope nejsou data o MAC adresach", value="bez dat"
                )
            ]

        baseline_instances = (ctx.baseline or {}).get("evpn_mac", {})
        tolerance = float(ctx.options(self.id)["tolerance_percent"])
        many = len(instances) > 1
        units = _service_units(ctx)

        findings = []
        for instance in sorted(instances):
            data = instances[instance]
            baseline = baseline_instances.get(instance, {})

            def label(text: str) -> str:
                return qualified(text, instance) if many else text

            subject_vlans = data.get("vlans", {})
            baseline_vlans = baseline.get("vlans", {})
            # Union se baseline: count vypis mrtvou domenu vubec neuvadi,
            # iterace jen pres subject by jeji zmizeni tise zahodila.
            for vlan in sorted(
                set(subject_vlans) | set(baseline_vlans),
                key=lambda v: int(v) if v.isdigit() else 0,
            ):
                if units.active and units.vlans and vlan not in units.vlans:
                    continue
                subject_entry = subject_vlans.get(vlan)
                baseline_entry = baseline_vlans.get(vlan)
                domain = (subject_entry or baseline_entry).get("domain")
                row_label = label(f"{domain} MAC count" if domain else "MAC count")
                if subject_entry is None:
                    findings.append(
                        _mac_compare_finding(
                            row_label, int(baseline_entry["count"]), 0, tolerance, ctx
                        )
                    )
                elif baseline_entry is None:
                    findings.append(
                        _mac_state_finding(row_label, int(subject_entry["count"]))
                    )
                else:
                    findings.append(
                        _mac_compare_finding(
                            row_label,
                            int(baseline_entry["count"]),
                            int(subject_entry["count"]),
                            tolerance,
                            ctx,
                        )
                    )

            baseline_interfaces = baseline.get("interfaces", {})
            # Nejdriv rozhrani, ktera vidi subjekt (EVO count vypis
            # interface-name nekdy nevrati - takove se proste neobjevi).
            # Ta, ktera videla jen baseline, resi samostatna smycka nize.
            for key in sorted(data.get("interfaces", {})):
                if units.active and key not in units.interfaces:
                    continue
                entry = data["interfaces"][key]
                domain = entry.get("domain")
                prefix = f"{domain} " if domain else ""
                row_label = label(f"{prefix}Interface {entry['name']} MAC count")
                baseline_entry = baseline_interfaces.get(key)
                if baseline_entry is None:
                    findings.append(
                        _mac_state_finding(row_label, int(entry["count"]))
                    )
                else:
                    findings.append(
                        _mac_compare_finding(
                            row_label,
                            int(baseline_entry["count"]),
                            int(entry["count"]),
                            tolerance,
                            ctx,
                        )
                    )

            subject_ifaces = data.get("interfaces", {})
            # Rozhrani, ktere baseline melo a subjekt o nem mlci, nesmi
            # tise zmizet - jinak by ztraceny IFL vypadal jako by v mac-vrf
            # nikdy nebyl (stejny duvod jako union pro per-VLAN vyse).
            for key in sorted(set(baseline_interfaces) - set(subject_ifaces)):
                if units.active and key not in units.interfaces:
                    continue
                baseline_entry = baseline_interfaces[key]
                domain = baseline_entry.get("domain")
                prefix = f"{domain} " if domain else ""
                row_label = label(f"{prefix}Interface {baseline_entry['name']} MAC count")
                b = int(baseline_entry["count"])
                findings.append(
                    Finding(
                        Outcome.BROKEN,
                        f"{row_label}: v baseline {b} MAC, v subjektu chybi",
                        label=row_label,
                        value="chybi",
                        baseline_value=str(b),
                        baseline={"mac_count": b},
                    )
                )
        return findings


def _mac_state_finding(label: str, count: int) -> Finding:
    if count == 0:
        return Finding(
            Outcome.BROKEN,
            f"{label}: 0 naucenych MAC adres",
            label=label,
            value="0",
            subject={"mac_count": 0},
        )
    return Finding(
        Outcome.OK,
        f"{label}: {count} naucenych MAC adres",
        label=label,
        value=str(count),
        subject={"mac_count": count},
    )


def _mac_compare_finding(
    label: str, baseline: int, subject: int, tolerance: float, ctx: CheckContext
) -> Finding:
    change = percent_change(baseline, subject)
    details: dict[str, Any] = {"tolerance_percent": tolerance}
    if change is not None:
        details["change_percent"] = round(change, 1)

    # Stejna trojice poli jako u BGP counteru (AR-4): hodnota, drivejsi
    # hodnota a hotovy rozdil. Bez baseline_value zustal sloupec ZMENA
    # u MAC prazdny, prestoze check obe cisla znal.
    presentation = {
        "value": str(subject),
        "baseline_value": str(baseline),
        "delta": f"{subject - baseline:+d}" if subject != baseline else None,
    }

    if change is not None and change < tolerance:
        return Finding(
            Outcome.BROKEN,
            f"{label}: pocet MAC klesl {baseline} -> {subject}, "
            f"prah je {tolerance:.0f} %",
            label=label,
            baseline={"mac_count": baseline},
            subject={"mac_count": subject},
            details=details,
            **presentation,
        )
    if subject == 0:
        outcome = unchanged_or(Outcome.BROKEN, ctx, "evpn_mac", same=baseline == 0)
        return Finding(
            outcome,
            f"{label}: 0 naucenych MAC adres (baseline {baseline})" + suffix(outcome),
            label=label,
            baseline={"mac_count": baseline},
            subject={"mac_count": 0},
            details=details,
            **presentation,
        )
    return Finding(
        Outcome.OK,
        f"{label}: {subject} MAC adres, v toleranci {tolerance:.0f} %",
        label=label,
        baseline={"mac_count": baseline},
        subject={"mac_count": subject},
        details=details,
        **presentation,
    )
