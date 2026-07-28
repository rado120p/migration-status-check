"""Conformance testy svu collector -> check (AR-6).

Global Constraints Planu 2 to vyzaduji vyslovne: klic mimo kontrakt
fact-schematu zpusobi, ze check tise vrati SKIP. Offline testy Planu 1 to
nechyti, protoze jejich fixtures si data staveji ze stejnych selektoru jako
scope, takze jsou konzistentni z konstrukce.

Tady se proto obe poloviny svu pripnou proti sobe: fakta vyrobi **skutecne
collectory** z **nahraneho XML z laborky** a projdou **skutecnymi checky**.
Kdyz collector prejmenuje klic, tenhle test spadne - misto aby cely nastroj
zacal mlcet.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from lxml import etree

from migration_validator import api
from migration_validator.collectors.arp import ArpCollector
from migration_validator.collectors.bgp import BgpCollector
from migration_validator.collectors.evpn import (
    EvpnEsiCollector,
    EvpnMacCollector,
    EvpnVpwsCollector,
)
from migration_validator.collectors.interfaces import InterfacesCollector
from migration_validator.collectors.nd import NdCollector
from migration_validator.models.inventory import load_inventory
from migration_validator.models.result import Status
from migration_validator.models.snapshot import CaptureMeta, DeviceMeta, Snapshot
from migration_validator.scoping.builder import build_scopes

NOW = "2026-07-27T10:30:00Z"

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
RPC_ROOT = FIXTURES / "rpc"

# Ktere zarizeni z laborky odpovida ktere nahravce.
DEVICES = {
    "junos": ("172.20.20.4", FIXTURES / "172.20.20.4.yml"),
    "junos-evo": ("172.20.20.5", FIXTURES / "172.20.20.5.yml"),
}

COLLECTORS = (
    InterfacesCollector(),
    ArpCollector(),
    NdCollector(),
    BgpCollector(),
    EvpnVpwsCollector(),
    EvpnEsiCollector(),
    EvpnMacCollector(),
)


def _fixture_paths(platform: str, collector) -> list[Path]:
    """Vsechny nahravky oblasti - collector muze mit vic RPC (evpn_mac na MX).

    `record` uklada prvni RPC pod jmenem oblasti a dalsi s poradovym cislem.
    Kdyby se tady cetla jen prvni, MX vlan-based instance by v faktech
    chybela a test by tvrdil mensi pokryti, nez capture ve skutecnosti ma.
    """
    root = RPC_ROOT / platform
    paths = [root / f"{collector.name}.xml"]
    paths += [
        root / f"{collector.name}.{index + 1}.xml"
        for index in range(1, len(collector.rpc_names(platform)))
    ]
    return paths


def _facts_from_recorded_xml(platform: str) -> dict:
    """Fakta presne tak, jak by je vyrobil capture - bez rucniho dolepovani."""
    facts = {}
    for collector in COLLECTORS:
        merged: Any = None
        for path in _fixture_paths(platform, collector):
            if not path.exists():
                pytest.skip(f"chybi fixture {path}")
            parsed = collector.parse(etree.parse(str(path)).getroot(), platform)
            merged = parsed if merged is None else _merge(merged, parsed)
        facts[collector.name] = merged
    return facts


def _merge(left: Any, right: Any) -> Any:
    """Slouceni vysledku vic RPC stejne oblasti."""
    if isinstance(left, list):
        return left + right
    combined = dict(left)
    for key, value in right.items():
        if key in combined and isinstance(value, dict):
            combined[key] = {**combined[key], **value}
        else:
            combined[key] = value
    return combined


def _snapshot(platform: str) -> Snapshot:
    address, inventory_path = DEVICES[platform]
    inventory = load_inventory(str(inventory_path))
    scopes = build_scopes(inventory)
    return Snapshot(
        device=DeviceMeta(address=address, platform=platform),
        capture=CaptureMeta(
            started_at=NOW,
            finished_at=NOW,
            phase=None,
            collectors={c.name: {"status": "ok"} for c in COLLECTORS},
        ),
        facts=_facts_from_recorded_xml(platform),
        probes={"ping": []},
        scopes=scopes,
        inventory=inventory.entries,
    )


@pytest.mark.parametrize("platform", ("junos", "junos-evo"))
def test_collector_keys_match_contract(platform):
    """Klice, ktere collector vyrobi, jsou presne ty, ktere Scope.select filtruje."""
    facts = _facts_from_recorded_xml(platform)
    assert set(facts) == {c.name for c in COLLECTORS}


@pytest.mark.parametrize("platform", ("junos", "junos-evo"))
def test_evpn_instances_are_keyed_by_routing_instance(platform):
    """evpn_vpws a evpn_mac se klicuji nazvem routing-instance, ne rozhranim.

    Scope je filtruje pres 'name in selectors.routing_instances', takze klic
    s nazvem rozhrani by znamenal, ze se nikdy nepotkaji.
    """
    facts = _facts_from_recorded_xml(platform)
    _, inventory_path = DEVICES[platform]
    scopes = build_scopes(load_inventory(str(inventory_path)))
    known = {
        instance for scope in scopes for instance in scope.selectors.routing_instances
    }

    for area in ("evpn_vpws", "evpn_mac"):
        keys = set(facts[area])
        if not keys:
            continue
        assert keys & known, (
            f"{platform}/{area}: zadny klic {sorted(keys)} neodpovida "
            f"routing-instance ze scopu {sorted(known)}"
        )


@pytest.mark.parametrize("platform", ("junos", "junos-evo"))
def test_esi_interface_matches_a_scope(platform):
    """evpn_esi.interface musi byt nazev, ktery scope matchne (spec: krehke misto)."""
    facts = _facts_from_recorded_xml(platform)
    entries = facts["evpn_esi"]
    if not entries:
        pytest.skip(f"{platform} nema zadny ESI segment")

    _, inventory_path = DEVICES[platform]
    scopes = build_scopes(load_inventory(str(inventory_path)))

    for esi, data in entries.items():
        interface = str(data["interface"])
        assert any(
            scope.selectors.matches_interface(interface) for scope in scopes
        ), f"{platform}: ESI {esi} hlasi rozhrani {interface!r}, ktere zadny scope nematchne"


@pytest.mark.parametrize("platform", ("junos", "junos-evo"))
def test_interfaces_reach_their_scopes(platform):
    """Rozhrani z collectoru se musi potkat se selektory scopu."""
    snapshot = _snapshot(platform)
    service_scopes = [scope for scope in snapshot.scopes if not scope.is_device]
    assert service_scopes

    selected = [
        scope
        for scope in service_scopes
        if scope.select(snapshot.facts, snapshot.probes)["interfaces"]
    ]
    assert selected, f"{platform}: zadny scope nevidel ani jedno rozhrani"


@pytest.mark.parametrize("platform", ("junos", "junos-evo"))
def test_checks_produce_real_verdicts_not_all_skip(platform):
    """Nad skutecnymi daty musi checky vydat verdikt, ne mlcet.

    Tohle je vlastni pointa conformance testu: SKIP neni FAIL, takze
    prejmenovany klic by jinak prosel bez povsimnuti.
    """
    result = api.evaluate(_snapshot(platform), now=NOW)
    checks = [check for scope in result.scopes for check in scope.checks]
    assert checks

    verdicts = [check for check in checks if check.status is not Status.SKIP]
    assert verdicts, f"{platform}: vsechny checky vratily SKIP"


@pytest.mark.parametrize(
    "platform,check_id",
    [
        ("junos", "interface_state"),
        ("junos-evo", "interface_state"),
        ("junos-evo", "evpn_mac_count"),
        ("junos-evo", "evpn_esi_status"),
        ("junos-evo", "evpn_vpws_status"),
    ],
)
def test_specific_check_sees_data(platform, check_id):
    """Kazda oblast zvlast - aby selhani ukazalo, ktery sev se rozpojil."""
    result = api.evaluate(_snapshot(platform), now=NOW)
    matching = [
        check
        for scope in result.scopes
        for check in scope.checks
        if check.id == check_id
    ]
    assert matching, f"{platform}: check {check_id} se vubec nespustil"
    assert any(check.status is not Status.SKIP for check in matching), (
        f"{platform}: check {check_id} vratil jen SKIP - collector emituje "
        f"jine klice, nez check konzumuje"
    )
