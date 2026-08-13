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
from typing import Any

from lxml import etree

import migration_validator.collectors.all  # noqa: F401  (registrace)
from migration_validator.collectors.base import CollectorError
from migration_validator.collectors.registry import all_collectors, collectors_for
from migration_validator.connection.junos import detect_platform, device_meta
from migration_validator.models.inventory import Inventory
from migration_validator.models.snapshot import CaptureMeta, Snapshot
from migration_validator.probes.ping import DEFAULT_COUNT, resolve_targets, run_ping
from migration_validator.scoping.builder import build_scopes

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
    baseline: Snapshot | None = None,
) -> Snapshot:
    started_at = now or _timestamp()
    platform = detect_platform(device)

    selected = _select_collectors(platform, collector_names)

    facts: dict[str, Any] = {}
    status: dict[str, dict[str, Any]] = {}

    for collector in selected:
        if record_raw is not None:
            _record(Path(record_raw), platform, collector.name, device, collector)
        try:
            facts[collector.name] = collector.collect(device, platform)
            status[collector.name] = {"status": "ok"}
        except CollectorError as error:
            # Oblast zustane prazdna se spravnym typem - check ji uvidi jako
            # chybejici a diky failed_collectors() vrati SKIP, nikdy PASS.
            facts[collector.name] = _empty_for(collector.name)
            status[collector.name] = {"status": "error", "message": str(error)}

    scopes = build_scopes(inventory) if inventory is not None else []

    pings: list[dict[str, Any]] = []
    if scopes:
        baseline_arp = baseline.facts.get("arp", []) if baseline is not None else None
        baseline_nd = baseline.facts.get("nd", []) if baseline is not None else None
        for target in resolve_targets(
            scopes,
            facts.get("arp", []),
            facts.get("nd", []),
            baseline_arp=baseline_arp,
            baseline_nd=baseline_nd,
        ):
            pings.append(run_ping(device, target, count=ping_count))

    return Snapshot(
        device=device_meta(device, address),
        capture=CaptureMeta(
            started_at=started_at,
            finished_at=now or _timestamp(),
            phase=phase,
            collectors=status,
        ),
        facts=facts,
        probes={"ping": pings},
        scopes=scopes,
        inventory=inventory.entries if inventory is not None else None,
    )
