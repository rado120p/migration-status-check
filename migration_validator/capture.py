"""Orchestrace fazi capture.

Poradi: bulk collectory -> scopy -> resolve ping cilu -> ping probe -> freeze.
Zavislost ARP -> ping se odehrava cela tady, takze checky uz jsou navzajem
nezavisle.

Selhani jednoho collectoru nezrusi capture - zapise se do capture.collectors
a checky, ktere tu oblast potrebuji, dostanou SKIP.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from lxml import etree

import migration_validator.collectors.all  # noqa: F401  (registrace)
from migration_validator.collectors.base import CollectorError
from migration_validator.collectors.registry import all_collectors, collectors_for
from migration_validator.connection.junos import detect_platform, device_meta
from migration_validator.models.inventory import Inventory
from migration_validator.models.snapshot import CaptureMeta, Snapshot
from migration_validator.probes.ping import DEFAULT_COUNT, resolve_targets, run_ping
from migration_validator.scoping.builder import build_scopes

# volano jako (krok, stav, zprava): stav je "start" | "ok" | "error",
# krok je jmeno kolektoru nebo "ping"
ProgressCallback = Callable[[str, str, str | None], None]

LIST_AREAS = frozenset({"arp", "nd"})


def _empty_for(area: str) -> Any:
    """Prazdna hodnota spravneho typu pro oblast, jejiz collector selhal."""
    return [] if area in LIST_AREAS else {}


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _select_collectors(platform: str, names: list[str] | None):
    available = collectors_for(platform)
    if names is None:
        return available

    known = {collector.name for collector in all_collectors()}
    unknown = [name for name in names if name not in known]
    if unknown:
        raise ValueError(f"neznamy collector: {', '.join(unknown)}")

    return [collector for collector in available if collector.name in names]


def _record(xml_root: Path, platform: str, name: str, device: Any, collector) -> None:
    """Ulozi syrove RPC XML pro pozdejsi pouziti jako fixture.

    Collector s vice RPC uklada kazde zvlast - prvni pod jmenem oblasti,
    dalsi s poradovym cislem. Jinak by fixture nesla jen cast dat.
    """
    target = Path(xml_root) / platform
    target.mkdir(parents=True, exist_ok=True)

    for index, (rpc_name, rpc_kwargs) in enumerate(collector.rpc_calls(platform)):
        try:
            xml = getattr(device.rpc, rpc_name)(**rpc_kwargs)
        except Exception:  # noqa: BLE001 - nahravani je best effort
            continue
        suffix = "" if index == 0 else f".{index + 1}"
        (target / f"{name}{suffix}.xml").write_bytes(
            etree.tostring(xml, pretty_print=True)
        )


def _merged_baseline_entries(
    snapshots: list[Snapshot], area: str
) -> list[dict[str, Any]]:
    """Sjednoceni ARP/ND zaznamu vice pre snimku, dedup podle IP.

    Prvni vyskyt vyhrava - poradi snimku urcuje cli (poradi mappingu).
    """
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for snapshot in snapshots:
        for entry in snapshot.facts.get(area, []):
            ip = entry.get("ip")
            if ip is None or ip in seen:
                continue
            seen.add(ip)
            merged.append(entry)
    return merged


def capture_device(
    device: Any,
    address: str,
    *,
    inventory: Inventory | None = None,
    collector_names: list[str] | None = None,
    phase: str | None = None,
    ping_count: int = DEFAULT_COUNT,
    now: str | None = None,
    record_raw: str | Path | None = None,
    baselines: list[Snapshot] | None = None,
    service_types: list[str] | None = None,
    on_progress: ProgressCallback | None = None,
) -> Snapshot:
    started_at = now or _timestamp()
    platform = detect_platform(device)

    selected = _select_collectors(platform, collector_names)

    facts: dict[str, Any] = {}
    status: dict[str, dict[str, Any]] = {}

    for collector in selected:
        if on_progress is not None:
            on_progress(collector.name, "start", None)
        if record_raw is not None:
            _record(Path(record_raw), platform, collector.name, device, collector)
        try:
            facts[collector.name] = collector.collect(device, platform)
            status[collector.name] = {"status": "ok"}
            if on_progress is not None:
                on_progress(collector.name, "ok", None)
        except CollectorError as error:
            # Oblast zustane prazdna se spravnym typem - check ji uvidi jako
            # chybejici a diky failed_collectors() vrati SKIP, nikdy PASS.
            facts[collector.name] = _empty_for(collector.name)
            status[collector.name] = {"status": "error", "message": str(error)}
            if on_progress is not None:
                on_progress(collector.name, "error", str(error))

    scopes = build_scopes(inventory) if inventory is not None else []

    pings: list[dict[str, Any]] = []
    ping_skipped: list[dict[str, Any]] = []
    if scopes:
        if service_types is None:
            ping_scopes = scopes
        else:
            allowed = set(service_types)
            ping_scopes = [s for s in scopes if s.service_type in allowed]
            # Marker misto ticha: evaluate z nej udela SKIP s duvodem,
            # jinak by odfiltrovany ping vypadal jako "bez cile".
            ping_skipped = [
                {"scope_id": s.id, "reason": "mimo profil"}
                for s in scopes
                if s.kind == "service" and s.service_type not in allowed
            ]
        baseline_arp = (
            _merged_baseline_entries(baselines, "arp") if baselines else None
        )
        baseline_nd = (
            _merged_baseline_entries(baselines, "nd") if baselines else None
        )
        targets = list(
            resolve_targets(
                ping_scopes,
                facts.get("arp", []),
                facts.get("nd", []),
                baseline_arp=baseline_arp,
                baseline_nd=baseline_nd,
            )
        )
        if on_progress is not None:
            on_progress("ping", "start", f"{len(targets)} cilu")
        for target in targets:
            pings.append(run_ping(device, target, count=ping_count))
        if on_progress is not None:
            on_progress("ping", "ok", None)

    return Snapshot(
        device=device_meta(device, address),
        capture=CaptureMeta(
            started_at=started_at,
            finished_at=now or _timestamp(),
            phase=phase,
            collectors=status,
        ),
        facts=facts,
        probes={"ping": pings, "ping_skipped": ping_skipped},
        scopes=scopes,
        inventory=inventory.entries if inventory is not None else None,
    )
