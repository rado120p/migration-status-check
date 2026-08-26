# Core transit/loopback split + protokolové checky — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rozdělit službu Core na subtype `transit`/`loopback` a dát každé roli vlastní sadu checků (IS-IS adjacency/interface/overview, LDP, PIM, MPLS interface, BFD na transitu; interní BGP peeři na lo0.0).

**Architecture:** Parser klasifikuje Core subtype a nově parsuje PIM záměr a interní BGP peery (inventory schema 7). Šest nových collectorů plní šest nových fact areas (snapshot schema 11). `Scope.select()` je rozvádí per-interface, checky se váží přes nový `service_subtypes` gate.

**Tech Stack:** Python 3.11+, lxml, PyEZ (jen collectory/record), pytest.

**Spec:** `docs/superpowers/specs/2026-08-26-core-transit-loopback-checks-design.md`

## Global Constraints

- Komentáře a docstringy česky, stylem okolního kódu (vysvětlují PROČ/omezení, ne co dělá další řádka).
- TDD: každý krok nejdřív failing test, pak minimální implementace.
- Stav se nikdy nefabuluje: collector nesyntetizuje "Down" řádky; rozhraní chybějící ve výpisu = chybějící klíč, význam absence určuje check.
- Fixtures: žádná vymyšlená XML struktura. Syntetické tvary v testech se odvozují ze zachycených souborů (editace hodnot, ne struktury) a komentují se.
- Tvrzení o mutantovi v docstringu se ověřuje spuštěním toho mutanta (projektová paměť: tvrzeni-o-mutantovi-zastarava).
- `INVENTORY_SCHEMA_VERSION` 6 → **7** (Task 2), `SCHEMA_VERSION` snapshotu 10 → **11** (Task 5). Bumpy se dělají právě jednou.
- Labely řádků reportu přesně dle specu (anglické: `IS-IS adjacency state`, `LDP neighbor status`, …), hodnoty absence česky (`chybí v outputu`).
- Lab: heslo `MIG_LAB_PASSWORD` je v `~/.bashrc` POD non-interactive guardem — načíst přes explicitní eval, např. `export MIG_LAB_PASSWORD=$(bash -ic 'echo $MIG_LAB_PASSWORD' 2>/dev/null)`.
- Testy se spouštějí přes `pyats-venv` prostředí projektu: `pyats-venv/bin/python -m pytest` (ověř `ls pyats-venv/bin/` na začátku; pokud venv chybí, použij systémový pytest projektu).

---

### Task 1: Lab capture šesti nových RPC + ověření tvarů

**Files:**
- Create: `tests/fixtures/rpc/junos/isis_adjacency.xml`, `isis_interface.xml`, `isis_overview.xml`, `ldp_neighbor.xml`, `pim_neighbor.xml`, `mpls_interface.xml`
- Create: totéž pod `tests/fixtures/rpc/junos-evo/`
- Create (scratch, necommituje se): `<scratchpad>/capture_core_rpcs.py`

**Interfaces:**
- Consumes: `migration_validator.connection.junos.detect_platform(device)`, PyEZ `Device`.
- Produces: 12 fixture souborů; poznámkový blok v commit message s ověřenými jmény elementů (viz krok 4), na který se odkazují Tasky 5–6.

- [ ] **Step 1: Napiš scratch skript**

```python
"""Jednorázový capture šesti nových RPC z laborky. Necommituje se."""
import os, sys
from pathlib import Path
from lxml import etree
from jnpr.junos import Device
from migration_validator.connection.junos import detect_platform

RPCS = [
    ("get_isis_adjacency_information", {"detail": True}, "isis_adjacency"),
    ("get_isis_interface_information", {"detail": True}, "isis_interface"),
    ("get_isis_overview_information", {}, "isis_overview"),
    ("get_ldp_neighbor_information", {"detail": True}, "ldp_neighbor"),
    ("get_pim_neighbors_information", {}, "pim_neighbor"),
    ("get_mpls_interface_information", {}, "mpls_interface"),
]

ROOT = Path("tests/fixtures/rpc")

for host in ("172.20.20.4", "172.20.20.5"):
    with Device(host=host, user="admin", passwd=os.environ["MIG_LAB_PASSWORD"],
                normalize=True) as dev:
        platform = detect_platform(dev)
        print(f"{host} -> {platform}")
        for rpc_name, kwargs, area in RPCS:
            try:
                reply = getattr(dev.rpc, rpc_name)(**kwargs)
            except Exception as error:
                print(f"  {rpc_name}: SELHALO - {error}", file=sys.stderr)
                continue
            target = ROOT / platform / f"{area}.xml"
            target.write_bytes(etree.tostring(reply, pretty_print=True))
            print(f"  {target}")
```

- [ ] **Step 2: Načti heslo a spusť**

Run: `export MIG_LAB_PASSWORD=$(bash -ic 'echo $MIG_LAB_PASSWORD' 2>/dev/null) && pyats-venv/bin/python <scratchpad>/capture_core_rpcs.py`
Expected: 12 souborů (6 na platformu). Pokud některé RPC na jedné platformě selže (např. PIM nenakonfigurován), zapiš to — prázdný/chybějící výstup je nález pro návrh testů, ne důvod soubor fabulovat.

- [ ] **Step 3: Ověř tvary proti předpokladům specu**

Otevři každý soubor a zapiš (do commit message a jako komentář k Taskům 5–6):
- jméno per-entry elementu a namespace (`isis-adjacency`, `isis-interface`, `isis-overview`, `ldp-neighbor`, `pim-neighbor`, `mpls-interface` — očekává se xmlns junos-routing, proto collectory iterují přes `iter("{*}...")`),
- kde sedí jméno rozhraní u LDP (`interface-name`?) a u PIM (`pim-interface-name`?) — spec to potřebuje jako klíč,
- přesný tvar atributu sekund (`junos:seconds` na `ldp-up-time` / `pim-neighbor-uptime`),
- zda `show mpls interface` nese `interface-name` + `mpls-interface-state`,
- zda lo0.0 opravdu figuruje v LDP výpisu (spec: zahazuje se při parsování).

**Pokud se reálný tvar liší od specu, oprav předpoklad v tomto plánu (Tasky 5–6) a poznamenej to — implementátor collectoru pracuje podle změřeného tvaru, ne podle plánem odhadnutého.**

- [ ] **Step 4: Commit fixtures**

```bash
git add tests/fixtures/rpc/junos/*.xml tests/fixtures/rpc/junos-evo/*.xml
git commit -m "test: fixtures sesti novych RPC z laborky (.4/.5)

Overene tvary: <sem vloz zjisteni ze Step 3>"
```

---

### Task 2: Parser — Core subtype transit/loopback, inventory schema 7

**Files:**
- Modify: `migration_validator/parsers/core.py` (`_detect_service`, okolí řádku 1497)
- Modify: `migration_validator/models/inventory.py` (`INVENTORY_SCHEMA_VERSION`)
- Test: `tests/parsers/test_core_subtype.py`

**Interfaces:**
- Consumes: stávající `_detect_service(...) -> (service_type, service_subtype, confidence, reasons)`.
- Produces: Core entry nese `service_subtype` `"loopback"` (lo0.\*) nebo `"transit"`. Tasky 7+ čtou `scope.key.service_subtype`.

- [ ] **Step 1: Failing test**

```python
"""Core se deli na subtype transit/loopback — lo0 a tranzitni port uz nesmi
sdilet jeden profil checku (spec 2026-08-26)."""
import pytest
from lxml import etree
from migration_validator.parsers.mx import JunosServiceParser
from migration_validator.parsers.evo import JunosEvoAcxServiceParser

CONFIG = """
<configuration>
  <interfaces>
    <interface><name>ge-0/0/0</name>
      <unit><name>0</name><family><iso/><mpls/><inet><address><name>10.1.2.1/31</name></address></inet></family></unit>
    </interface>
    <interface><name>lo0</name>
      <unit><name>0</name><family><iso/><inet><address><name>150.0.0.11/32</name></address></inet></family></unit>
    </interface>
  </interfaces>
</configuration>
"""

@pytest.mark.parametrize("parser_cls", [JunosServiceParser, JunosEvoAcxServiceParser])
def test_core_subtype_transit_vs_loopback(parser_cls):
    services = parser_cls(etree.fromstring(CONFIG)).parse()
    by_iface = {s.interface: s for s in services if s.service_type == "Core"}
    assert by_iface["ge-0/0/0.0"].service_subtype == "transit"
    assert by_iface["lo0.0"].service_subtype == "loopback"
```

Pozn.: přesný tvar CONFIG XML srovnej s existujícími parser testy (`tests/parsers/test_family_split.py`) — pokud tam testy budují konfiguraci jinak (wrapper `<rpc-reply>`, helper), použij jejich idiom, ne tenhle doslovný blok.

- [ ] **Step 2: Ověř FAIL**

Run: `pyats-venv/bin/python -m pytest tests/parsers/test_core_subtype.py -v`
Expected: FAIL — subtype je `None`.

- [ ] **Step 3: Implementace v `_detect_service`**

V Core větvi (`parsers/core.py` ~1497):

```python
if {"iso", "mpls"} & family_set:
    reasons.append("Rozhraní používá family iso nebo family mpls.")
    subtype = "loopback" if interface.name.startswith("lo0") else "transit"
    return ("Core", subtype, "high", reasons)
```

(`interface.name` je jméno logické jednotky `lo0.0` — ověř přesný atribut na `InterfaceConfig`, řádek ~129; startswith("lo0") pokrývá lo0.0 i lo0.1.)

- [ ] **Step 4: Bump inventory schema**

`models/inventory.py`: `INVENTORY_SCHEMA_VERSION = 7` + doplň komentář nad konstantu ve stylu snapshotů (`# 7: Core entry nese service_subtype transit|loopback ...`). Najdi a uprav testy verze: `grep -rn "INVENTORY_SCHEMA_VERSION\|schema_version.*6" tests/` a zvyš očekávané hodnoty.

- [ ] **Step 5: Testy zeleně + celá suita**

Run: `pyats-venv/bin/python -m pytest tests/parsers/test_core_subtype.py tests/ -x -q`
Expected: PASS. Padnou-li testy s natvrdo zapsaným `service_subtype: None` u Core, oprav jejich očekávání — to je zamýšlená změna.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: Core subtype transit/loopback, inventory schema 7"
```

---

### Task 3: Parser — PIM záměr do `protocol`

**Files:**
- Modify: `migration_validator/parsers/core.py`
- Test: `tests/parsers/test_pim_intent.py`

**Interfaces:**
- Consumes: `self.global_protocols_by_interface: dict[str, set[str]]` (plněno v `_parse_global_eline_interfaces`, čteno v `_collect_protocols`).
- Produces: služba s rozhraním pod `protocols pim interface <name>` má `"pim"` v `ServiceEntry.protocol`. Task 10 na tom staví gate.

- [ ] **Step 1: Failing test**

```python
"""PIM zamer: 'pim' v protocol znamena 'neighbor je ocekavany' (spec 2026-08-26).
Bez zaznamu check mlci — parsovani je jediny zdroj toho ocekavani."""
```

Test (parametrizovaně přes oba parsery, stejný config idiom jako Task 2): konfigurace s `protocols pim interface ge-0/0/0.0` a druhým Core rozhraním bez PIM; assert `"pim" in service.protocol` jen u prvního. Přidej variantu s PIM pod routing-instance (`routing-instances X protocols pim interface ...`), pokud config takový tvar v laborce má — ověř na zachycené konfiguraci; pokud lab RI-PIM nemá, otestuj jen globální tvar a nech komentář proč.

- [ ] **Step 2: FAIL** — Run: `pyats-venv/bin/python -m pytest tests/parsers/test_pim_intent.py -v`

- [ ] **Step 3: Implementace**

V `__init__`/`_parse_default_bgp_neighbors` okolí přidej volání `self._parse_global_protocol_interfaces("pim")`, které plní `global_protocols_by_interface` stejně jako `_parse_global_eline_interfaces` (`./protocols/pim//interface/name/text()` → `set.add("pim")`). Pro RI: `RoutingInstance.protocols` už nese jména protokolů z `child_names(node, "./protocols/*")` — `_collect_protocols` je do služby přidává; ověř, že to pro RI případ stačí (pak RI větev nepotřebuje nic navíc), a globální větev vyřeš přes `global_protocols_by_interface`.

Pozor: `_collect_protocols` přidává protokoly i podle **fyzického** jména (`interface.physical_name`). PIM se konfiguruje na logické jednotce — plň klíč logickým jménem, ať `"pim"` nedostanou sesterské unity.

- [ ] **Step 4: PASS + suita** — Run: `pyats-venv/bin/python -m pytest tests/parsers/ tests/ -x -q`

- [ ] **Step 5: Commit** — `git commit -am "feat: parser cte protocols pim do ServiceEntry.protocol"`

---

### Task 4: Parser — interní BGP peeři na lo0.0

**Files:**
- Modify: `migration_validator/parsers/core.py` (`_parse_bgp_neighbors`, `_parse_default_bgp_neighbors`, `_assign_bgp_neighbors`, `_assign_bfd`)
- Test: `tests/parsers/test_internal_bgp.py`

**Interfaces:**
- Consumes: `_parse_bgp_neighbors(node, bgp_xpath) -> (neighbors, inactive)` (řádek ~571), `self.default_bgp_neighbors`.
- Produces: `self.default_bgp_neighbors_internal: list[str]`; Core-loopback služba má interní peery v `bgp_neighbor` (+ `"bgp"` v `protocol`). Task 7 je pak dostane do selektorů zdarma (builder už `bgp_neighbor` kopíruje).

- [ ] **Step 1: Failing testy**

`tests/parsers/test_internal_bgp.py`, parametrizovaně přes oba parsery:

1. `type internal` na group → peer skončí v `bgp_neighbor` lo0.0 Core služby, ne v Internet službě.
2. `type external` na group → peer se na lo0.0 nedostane (chování Internet/IPVPN beze změny).
3. Bez `type`: `routing-options autonomous-system 65000` + `peer-as 65000` → internal (fallback); `peer-as 65001` → external.
4. Override: group `type external`, neighbor `type internal` → neighbor vyhrává.
5. lo0.0 Core služba s interními peery **nemá** žádné `bfd` záměry, i když globální `protocols bgp` BFD konfiguruje (gate z `_assign_bfd` docstringu).

Konfigurační kostra (přizpůsob idiomu parser testů):

```xml
<routing-options><autonomous-system><as-number>65000</as-number></autonomous-system></routing-options>
<protocols><bgp>
  <group><name>IBGP</name><type>internal</type>
    <neighbor><name>150.0.0.12</name></neighbor></group>
  <group><name>TRANSIT</name><type>external</type><peer-as>65010</peer-as>
    <neighbor><name>10.0.0.2</name></neighbor></group>
</bgp></protocols>
```

- [ ] **Step 2: FAIL** — Run: `pyats-venv/bin/python -m pytest tests/parsers/test_internal_bgp.py -v`

- [ ] **Step 3: Implementace**

a) Nová metoda vedle `_parse_bgp_neighbors` (interní se poznávají jen na globálním `protocols bgp` — RI peeři jsou zákaznické služby a tag nepotřebují):

```python
def _parse_internal_bgp_neighbors(self) -> list[str]:
    """Interni peer podle explicitniho type, fallback peer-as == local-as.

    Explicitni prikaz ma prednost (rozhodnuti 2026-08-26): AS porovnani
    je zachrana pro skupiny spolehajici na implicitni typovani Junosu,
    ne prvni instance pravdy.
    """
    local_as = first_text(
        self.config_xml, "./routing-options/autonomous-system/as-number/text()"
    )
    internal: list[str] = []
    for group in self.config_xml.xpath("./protocols/bgp/group"):
        group_type = first_text(group, "./type/text()")
        group_peer_as = first_text(group, "./peer-as/text()")
        for neighbor_node in group.xpath("./neighbor"):
            name = first_text(neighbor_node, "./name/text()")
            if not name or self._is_inactive(neighbor_node):
                continue
            peer_type = first_text(neighbor_node, "./type/text()") or group_type
            peer_as = first_text(neighbor_node, "./peer-as/text()") or group_peer_as
            if peer_type == "internal":
                internal.append(name)
            elif peer_type is None and local_as and peer_as == local_as:
                internal.append(name)
    return unique(internal)
```

(Neighbors přímo pod `bgp` bez group: ověř na zachycené lab konfiguraci, jestli tvar existuje — pokud ano, přidej druhou smyčku přes `./protocols/bgp/neighbor`.)

b) V `_parse_default_bgp_neighbors` ulož `self.default_bgp_neighbors_internal = self._parse_internal_bgp_neighbors()`.

c) V `_assign_bgp_neighbors` — interní peeři jdou na loopback, externí logika beze změny; navíc Internet větev interní peery nesmí chytit přes subnet match (loopback subnet /32 se s WAN rozhraními nepotká, ale gate je levný a explicitní):

```python
for service in services:
    if service.service_type == "Core" and service.service_subtype == "loopback":
        if self.default_bgp_neighbors_internal:
            service.bgp_neighbor = unique(
                service.bgp_neighbor + self.default_bgp_neighbors_internal
            )
            service.protocol = unique(service.protocol + ["bgp"])
            service.detection_reason.append(
                "Interni BGP peeri (type internal / shoda AS): "
                + ", ".join(self.default_bgp_neighbors_internal)
            )
        continue
    if service.service_type not in {"Internet", "IPVPN"}:
        continue
    ...  # stavajici telo
```

d) V `_assign_bfd` přidej gate + uprav docstring (podmínka z něj přestala platit — přepiš odstavec „Je to podmínka, na které tahle metoda stojí" na popis nového gate):

```python
if service.service_type == "Core":
    # Interni peeri BFD zamery nedostavaji (rozhodnuti 2026-08-26);
    # BFD na transitu resi bfd_transit_state bez zameru.
    continue
```

- [ ] **Step 4: PASS + suita** — Run: `pyats-venv/bin/python -m pytest tests/parsers/test_internal_bgp.py tests/ -x -q`
Expected: PASS. Pozor na testy NEZAŘAZENO/engine počítající interní peery mezi unassigned — jejich očekávání se mění záměrně (peer má nově vlastníka).

- [ ] **Step 5: Commit** — `git commit -am "feat: interni BGP peeri se mapuji na Core loopback"`

---

### Task 5: Collectory IS-IS (adjacency, interface, overview) + snapshot schema 11

**Files:**
- Create: `migration_validator/collectors/isis.py`
- Modify: `migration_validator/collectors/all.py`, `migration_validator/models/scope.py` (`FACT_AREAS`), `migration_validator/models/snapshot.py` (`SCHEMA_VERSION`), `tests/conftest.py` (`COLLECTOR_NAMES`)
- Test: `tests/collectors/test_isis.py`

**Interfaces:**
- Consumes: `Collector` base, `register`, fixtures z Tasku 1, tvary ověřené v Task 1 Step 3.
- Produces: areas `isis_adjacency: dict[iface, {system_name, state, ip_address, ipv6_address}]`, `isis_interface: dict[iface, {levels: dict[str, {passive: bool}]}]`, `isis_overview: {overload_enabled: bool}`. Pomocníky `_localname_text(node, name)` a `_seconds_attr(node)` importuje Task 6.

- [ ] **Step 1: Failing testy**

```python
"""IS-IS collectory. RPC odpovedi nesou xmlns junos-routing, proto se
iteruje pres {*} wildcard a texty ctou pres localname."""

def test_isis_adjacency_parses_fixture(rpc_fixture):
    from migration_validator.collectors.isis import IsisAdjacencyCollector
    data = IsisAdjacencyCollector().parse(rpc_fixture("junos", "isis_adjacency"), "junos")
    entry = data["ge-0/0/0.0"]      # jmeno rozhrani dle skutecne fixture
    assert entry["state"] == "Up"
    assert entry["system_name"]
    assert "ip_address" in entry and "ipv6_address" in entry

def test_isis_interface_levels(rpc_fixture):
    from migration_validator.collectors.isis import IsisInterfaceCollector
    data = IsisInterfaceCollector().parse(rpc_fixture("junos", "isis_interface"), "junos")
    lo0 = data["lo0.0"]
    assert lo0["levels"]["2"]["passive"] is True   # dle lab konfigurace

def test_isis_overview_overload_flag(rpc_fixture):
    from migration_validator.collectors.isis import IsisOverviewCollector
    data = IsisOverviewCollector().parse(rpc_fixture("junos", "isis_overview"), "junos")
    assert data == {"overload_enabled": True}      # dle skutecne fixture; kdyz lab overload nema, False + syntetika
```

Doplň syntetické varianty (XML string odvozený z fixture, komentovaný): adjacency `Down`, chybějící `global-ipv6-address` (klíč pak `None`), overview bez `isis-overload-enabled` → `False`. Přidej `junos-evo` parametrizaci (`@pytest.mark.parametrize("platform", ["junos", "junos-evo"])`) tam, kde obě fixtures nesou týž tvar.

- [ ] **Step 2: FAIL** — Run: `pyats-venv/bin/python -m pytest tests/collectors/test_isis.py -v`

- [ ] **Step 3: Implementace `collectors/isis.py`**

```python
"""Sber IS-IS stavu. Tri collectory v jednom modulu — sdileji namespace
helpery; odpovedi routing demonu nesou xmlns junos-routing, takze primy
iter('isis-adjacency') by nenasel nic."""

from __future__ import annotations
from typing import Any
from lxml import etree
from migration_validator.collectors.base import Collector
from migration_validator.collectors.registry import register


def _localname_text(node: etree._Element, name: str) -> str | None:
    for child in node.iter(f"{{*}}{name}"):
        text = (child.text or "").strip()
        return text or None
    return None


def _seconds_attr(node: etree._Element | None) -> int | None:
    """junos:seconds — URI atributu nese verzi OS, matchuje se localname."""
    if node is None:
        return None
    for key, value in node.attrib.items():
        if key.endswith("}seconds") or key == "seconds":
            try:
                return int(value)
            except ValueError:
                return None
    return None


@register
class IsisAdjacencyCollector(Collector):
    name = "isis_adjacency"

    def rpc_name(self, platform: str) -> str:
        return "get_isis_adjacency_information"

    def rpc_kwargs(self, platform: str) -> dict[str, Any]:
        return {"detail": True}

    def parse(self, xml: etree._Element, platform: str) -> dict[str, dict[str, Any]]:
        adjacencies: dict[str, dict[str, Any]] = {}
        for node in xml.iter("{*}isis-adjacency"):
            interface = _localname_text(node, "interface-name")
            if not interface:
                continue
            adjacencies[interface] = {
                "system_name": _localname_text(node, "system-name"),
                "state": _localname_text(node, "adjacency-state") or "unknown",
                "ip_address": _localname_text(node, "ip-address"),
                "ipv6_address": _localname_text(node, "global-ipv6-address"),
            }
        return adjacencies
```

`IsisInterfaceCollector` (`name = "isis_interface"`, `get_isis_interface_information`, `detail=True`): pro každý `{*}isis-interface` vezmi `interface-name` a projdi `{*}interface-level-data`; `levels[level] = {"passive": _localname_text(level_node, "passive") == "Passive"}`.

`IsisOverviewCollector` (`name = "isis_overview"`, `get_isis_overview_information`, bez kwargs):

```python
    def parse(self, xml, platform):
        overload = next(xml.iter("{*}isis-overload-enabled"), None)
        return {"overload_enabled": overload is not None}
```

- [ ] **Step 4: Registrace + schema**

- `collectors/all.py`: přidej `isis` do importu.
- `models/scope.py` `FACT_AREAS`: přidej `"isis_adjacency", "isis_interface", "isis_overview", "ldp_neighbor", "pim_neighbor", "mpls_interface"` (všech šest najednou — Task 6 už schema nemění). `_empty()` vrací pro nové areas `{}` (default) — beze změny.
- `models/snapshot.py`: `SCHEMA_VERSION = 11` + komentář `# 11: fact areas isis_adjacency/isis_interface/isis_overview/ldp_neighbor/pim_neighbor/mpls_interface (Core transit/loopback checky)`.
- `tests/conftest.py` `COLLECTOR_NAMES`: přidej všech šest jmen.

- [ ] **Step 5: PASS + suita** — Run: `pyats-venv/bin/python -m pytest tests/collectors/test_isis.py tests/ -x -q`
Expected: PASS; testy s natvrdo zapsanou schema_version 10 uprav (záměrná změna). `tests/collectors/test_conformance.py` nové collectory automaticky pokryje — pokud vyžaduje fixtures pro obě platformy, Task 1 je dodal.

- [ ] **Step 6: Commit** — `git commit -am "feat: IS-IS collectory, snapshot schema 11"`

---

### Task 6: Collectory LDP, PIM, MPLS interface

**Files:**
- Create: `migration_validator/collectors/ldp.py`, `pim.py`, `mpls.py`
- Modify: `migration_validator/collectors/all.py`
- Test: `tests/collectors/test_ldp.py`, `test_pim.py`, `test_mpls.py`

**Interfaces:**
- Consumes: `_localname_text`, `_seconds_attr` z `collectors/isis.py`; tvary ověřené v Task 1 Step 3 (jméno interface elementu u LDP/PIM převezmi odtud!).
- Produces: `ldp_neighbor: dict[iface, {neighbor_address, uptime_seconds}]`, `pim_neighbor: dict[iface, {neighbor_address, uptime_seconds}]`, `mpls_interface: dict[iface, {state}]`.

- [ ] **Step 1: Failing testy** — na fixtures z Tasku 1 (`rpc_fixture("junos", "ldp_neighbor")` atd.), plus syntetika: prázdný výpis → `{}` (absence = absence klíče, žádný syntetizovaný Down), LDP záznam na `lo0.0` → zahozen.

- [ ] **Step 2: FAIL** — Run: `pyats-venv/bin/python -m pytest tests/collectors/test_ldp.py tests/collectors/test_pim.py tests/collectors/test_mpls.py -v`

- [ ] **Step 3: Implementace** (vzor `ldp.py`; `pim.py`/`mpls.py` analogicky):

```python
@register
class LdpNeighborCollector(Collector):
    name = "ldp_neighbor"

    def rpc_name(self, platform: str) -> str:
        return "get_ldp_neighbor_information"

    def rpc_kwargs(self, platform: str) -> dict[str, Any]:
        return {"detail": True}

    def parse(self, xml: etree._Element, platform: str) -> dict[str, dict[str, Any]]:
        neighbors: dict[str, dict[str, Any]] = {}
        for node in xml.iter("{*}ldp-neighbor"):
            interface = _localname_text(node, "interface-name")  # jmeno dle Task 1
            if not interface or interface.startswith("lo0"):
                # lo0 vypisy se ignoruji (spec) - targeted session neni
                # stav tranzitniho linku
                continue
            uptime = next(node.iter("{*}ldp-up-time"), None)
            neighbors[interface] = {
                "neighbor_address": _localname_text(node, "ldp-neighbor-address"),
                "uptime_seconds": _seconds_attr(uptime),
            }
        return neighbors
```

PIM: `{*}pim-neighbor`, interface element dle Task 1 (pravděpodobně `pim-interface-name`), uptime z `{*}pim-neighbor-uptime`. MPLS: `{*}mpls-interface`, `{"state": _localname_text(node, "mpls-interface-state") or "unknown"}`, bez kwargs.

- [ ] **Step 4: PASS + suita** — Run: `pyats-venv/bin/python -m pytest tests/collectors/ tests/ -x -q`

- [ ] **Step 5: Commit** — `git commit -am "feat: LDP/PIM/MPLS interface collectory"`

---

### Task 7: Scoping — selekce nových areas, subtype gate, BFD podle rozhraní

**Files:**
- Modify: `migration_validator/models/scope.py` (`Selectors`, `Scope.select`, `Scope.service_subtype`), `migration_validator/scoping/builder.py`, `migration_validator/checks/base.py` (`service_subtypes`), `migration_validator/checks/bgp.py` (applies_to pro Core loopback)
- Test: `tests/models/test_scope_core.py`, rozšíření `tests/checks/test_base.py`, `tests/checks/test_bgp.py`

**Interfaces:**
- Consumes: areas z Tasků 5–6; `entry.protocol` z Tasku 3; BFD session `interface` pole (collector ho už zaznamenává — `collectors/bfd.py:52`).
- Produces:
  - `Scope.service_subtype -> str | None` (property: `self.key.service_subtype if self.key else None`),
  - `Selectors.protocols: list[str]` (builder plní z `entry.protocol`),
  - `Check.service_subtypes: ClassVar[frozenset[str] | None]` — AND ke stávajícímu `service_types`,
  - `select()` vrací šest nových klíčů; BGP checky běží i na Core loopback.

- [ ] **Step 1: Failing testy**

`tests/models/test_scope_core.py`:

```python
"""Selekce novych protokolovych areas. isis_overview je device-global a
patri jen loopback scopu — transit by s nim tvrdil mereni, ktere se ho netyka."""
```

1. Transit Core scope (selectors.interfaces=[`ge-0/0/0.0`], physical `ge-0/0/0`) dostane z `facts["isis_adjacency"]` jen své rozhraní; totéž `isis_interface`, `ldp_neighbor`, `pim_neighbor`, `mpls_interface`.
2. `isis_overview`: loopback scope (key.service_subtype="loopback") ho dostane celý; transit scope dostane `{}`; device scope celý.
3. BFD: session s `interface: "ge-0/0/0.0"` a peerem mimo `bgp_neighbors` se do transit-Core scopu dostane; do zákaznického (Internet) scopu se stejným rozhraním v selektorech NE (cesta přes rozhraní platí jen pro Core transit).
4. `Selectors.protocols` round-trip přes `to_dict`/`from_dict`.

`tests/checks/test_base.py` rozšíření: check se `service_types={"Core"}`, `service_subtypes={"transit"}` applies na transit scope, ne na loopback scope, ne na Internet; device scope vždy.

`tests/checks/test_bgp.py` rozšíření: `BgpSessionStateCheck().applies_to(core_loopback_scope) is True`, `applies_to(core_transit_scope) is False`, Internet/IPVPN beze změny.

- [ ] **Step 2: FAIL** — Run: `pyats-venv/bin/python -m pytest tests/models/test_scope_core.py tests/checks/test_base.py tests/checks/test_bgp.py -v`

- [ ] **Step 3: Implementace**

a) `models/scope.py`:

```python
@property
def service_subtype(self) -> str | None:
    return self.key.service_subtype if self.key else None
```

`Selectors`: přidej `protocols: list[str] = field(default_factory=list)` + do `to_dict` (`from_dict` je generický přes `cls().to_dict()` — jen ověř, že pole projde). V `select()`:

```python
per_interface_areas = (
    "isis_adjacency", "isis_interface", "ldp_neighbor",
    "pim_neighbor", "mpls_interface",
)
protocol_areas = {
    area: {
        name: data
        for name, data in (facts.get(area) or {}).items()
        if self.selectors.matches_interface(name)
    }
    for area in per_interface_areas
}
# Overview je tvrzeni o routeru, ne o lince - dostane ho jen scope,
# ktery router reprezentuje (lo0.0).
isis_overview = (
    dict(facts.get("isis_overview") or {})
    if self.service_subtype == "loopback"
    else {}
)
```

BFD rozšíření (u stávajícího `bfd` výběru):

```python
bfd = {
    peer: data
    for peer, data in (facts.get("bfd") or {}).items()
    if peer in self.selectors.bgp_neighbors
    or (
        # Transit Core nema BFD zamery ani peery v konfiguraci sluzby -
        # session na nej patri podle rozhrani (spec 2026-08-26).
        self.service_type == "Core"
        and self.service_subtype == "transit"
        and self.selectors.matches_interface(str(data.get("interface", "")))
    )
}
```

Device větev `select()`: šest nových areas pokrývá stávající smyčka přes `FACT_AREAS` — nic navíc.

b) `scoping/builder.py`: `Selectors(..., protocols=list(entry.protocol))` v service větvi.

c) `checks/base.py`:

```python
service_subtypes: ClassVar[frozenset[str] | None] = None
```

V `applies_to`, za stávající `service_types` podmínku:

```python
if self.service_subtypes is not None:
    if scope.service_subtype not in self.service_subtypes:
        return False
```

Do `describe()` přidej `"service_subtypes"` (sorted nebo None) — a rozšiř testy describe, pokud existují (`grep -rn "describe()" tests/checks/`).

d) `checks/bgp.py` — mixin nad oběma checky:

```python
class _AppliesToCoreLoopback:
    """BGP checky meri i interni peery na lo0.0 (spec 2026-08-26).

    service_types | {"Core"} nestaci - Core transit zadne peery nema a
    dostal by prazdne SKIP/FAIL radky. Gate na subtype je proto v
    applies_to, ne v datech.
    """

    def applies_to(self, scope):
        if scope.service_type == "Core":
            return scope.service_subtype == "loopback"
        return super().applies_to(scope)
```

`class BgpSessionStateCheck(_AppliesToCoreLoopback, Check)` a totéž `BgpPrefixCountsCheck`; `service_types` ponech `CUSTOMER_SERVICE_TYPES` (mixin řeší Core větev dřív). `checks/reachability.py` se nemění.

- [ ] **Step 4: PASS + suita** — Run: `pyats-venv/bin/python -m pytest tests/models/test_scope_core.py tests/checks/ tests/ -x -q`

- [ ] **Step 5: Commit** — `git commit -am "feat: scoping novych areas, service_subtypes gate, BGP na Core loopback"`

---

### Task 8: Check `isis_adjacency_state`

**Files:**
- Create: `migration_validator/checks/core_protocols.py`
- Modify: `migration_validator/checks/all.py` (import `core_protocols`)
- Test: `tests/checks/test_core_protocols.py`

**Interfaces:**
- Consumes: `ctx.subject["isis_adjacency"]`, `ctx.baseline`, `is_transit`/`qualified` z `checks.ifaces`, `Finding`/`Outcome` z models.
- Produces: modul `checks/core_protocols.py` s helpery `_scope_transit_interfaces(ctx)` a `format_uptime(seconds)` — Tasky 9–11 do něj přidávají další checky.

- [ ] **Step 1: Failing testy**

`tests/checks/test_core_protocols.py` — ctx builder podle idiomu `tests/checks/test_bfd.py` (scope s `kind="service"`, key `ScopeKey(None, "Core", "transit")`, selectors s interfaces). Případy:

1. Bez baseline, adjacency Up se všemi poli → 4 řádky: INFO name, PASS state, PASS IPv4, PASS IPv6.
2. Bez baseline, state Down → FAIL state; chybějící `ipv6_address` → FAIL na IPv6 řádku.
3. Rozhraní není v `isis_adjacency` → jediný řádek FAIL `chybí v outputu`.
4. Baseline: jiný `system_name` → WARN name; state Up nyní / Down v baseline → WARN state; state shodný → PASS; jiná `ip_address` → WARN.
5. Loopback scope → check se nespustí (`applies_to` False).

- [ ] **Step 2: FAIL** — Run: `pyats-venv/bin/python -m pytest tests/checks/test_core_protocols.py -v`

- [ ] **Step 3: Implementace**

```python
"""Checky protokolu Core transit/loopback (spec 2026-08-26).

Absence rozhrani ve vypisu je mereni, ne dira: FAIL 'chybi v outputu'.
Collector klice nesyntetizuje, takze tenhle modul je jedine misto, ktere
absenci vyklada."""

from __future__ import annotations
from typing import Any
from migration_validator.checks.base import Check, CheckContext, Mode
from migration_validator.checks.ifaces import is_transit, qualified
from migration_validator.checks.registry import register
from migration_validator.models.result import Finding, Outcome, Severity

MISSING = "chybí v outputu"
CORE = frozenset({"Core"})


def _scope_transit_interfaces(ctx: CheckContext) -> list[str]:
    """Merene jednotky bere ze selektoru (zamer), ne z faktu - rozhrani,
    ktere z vypisu zmizelo, musi dostat radek, ne ticho."""
    return sorted(
        name for name in ctx.scope.selectors.interfaces if is_transit(name)
    )


def format_uptime(seconds: int | None) -> str:
    if not seconds:
        return "0s"
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


@register
class IsisAdjacencyStateCheck(Check):
    id = "isis_adjacency_state"
    title = "Stav IS-IS adjacency"
    label = "IS-IS adjacency state"
    mode = Mode.BOTH
    requires = ("isis_adjacency",)
    requires_inventory = True
    service_types = CORE
    service_subtypes = frozenset({"transit"})
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        subject: dict[str, Any] = ctx.subject.get("isis_adjacency", {})
        baseline: dict[str, Any] = (ctx.baseline or {}).get("isis_adjacency", {})
        findings: list[Finding] = []
        for name in _scope_transit_interfaces(ctx):
            adj = subject.get(name)
            was = baseline.get(name)
            if adj is None:
                findings.append(Finding(
                    Outcome.BROKEN,
                    f"{name}: rozhrani neni v IS-IS adjacency vypisu",
                    label=qualified(self.label, name),
                    value=MISSING,
                    baseline_value=str((was or {}).get("state")) if was else None,
                ))
                continue
            findings.extend(self._rows(name, adj, was, ctx.has_baseline))
        return findings

    def _rows(self, name, adj, was, has_baseline):
        rows = []
        system = adj.get("system_name")
        if has_baseline and was is not None and system != was.get("system_name"):
            rows.append(Finding(
                Outcome.DEGRADED,
                f"{name}: IS-IS soused {system}, v baseline {was.get('system_name')}",
                label=qualified("IS-IS neighbor name", name),
                value=str(system), baseline_value=str(was.get("system_name")),
            ))
        else:
            outcome = Outcome.OK if has_baseline and was is not None else Outcome.INFO
            rows.append(Finding(
                outcome, f"{name}: IS-IS soused {system}",
                label=qualified("IS-IS neighbor name", name), value=str(system),
            ))

        state = str(adj.get("state", "unknown"))
        was_state = str(was.get("state")) if was else None
        if state != "Up":
            outcome = Outcome.BROKEN
        elif has_baseline and was_state is not None and was_state != "Up":
            # Up ted, ale v baseline nebyl - zlepseni je porad zmena
            outcome = Outcome.DEGRADED
        else:
            outcome = Outcome.OK
        rows.append(Finding(
            outcome, f"{name}: adjacency {state}",
            label=qualified(self.label, name),
            value=state, baseline_value=was_state,
        ))

        for label, key in (
            ("IS-IS neighbor IPv4 address", "ip_address"),
            ("IS-IS neighbor IPv6 address", "ipv6_address"),
        ):
            value = adj.get(key)
            was_value = was.get(key) if was else None
            if has_baseline and was is not None and value != was_value:
                outcome = Outcome.DEGRADED
            else:
                outcome = Outcome.OK if value is not None else Outcome.BROKEN
            rows.append(Finding(
                outcome,
                f"{name}: {label} {value or 'chybi'}",
                label=qualified(label, name),
                value=str(value) if value else MISSING,
                baseline_value=str(was_value) if was_value else None,
            ))
        return rows
```

`checks/all.py`: přidej `core_protocols` do importu.

- [ ] **Step 4: PASS + suita** — Run: `pyats-venv/bin/python -m pytest tests/checks/test_core_protocols.py tests/ -x -q`

- [ ] **Step 5: Commit** — `git commit -am "feat: check isis_adjacency_state"`

---

### Task 9: Checky `isis_interface_info` (role-aware) a `isis_overview`

**Files:**
- Modify: `migration_validator/checks/core_protocols.py`
- Test: `tests/checks/test_core_protocols.py` (rozšíření)

**Interfaces:**
- Consumes: `ctx.subject["isis_interface"]` (`levels: dict[str, {passive: bool}]`), `ctx.subject["isis_overview"]` (`{overload_enabled: bool}`), helpery z Tasku 8.
- Produces: check ids `isis_interface_info`, `isis_overview`.

- [ ] **Step 1: Failing testy**

`isis_interface_info` (běží na transit i loopback scopu):
1. Loopback: level "2" s `passive=True` → PASS level 2 + PASS passive; `passive=False` → FAIL passive.
2. Loopback/transit: přítomný level "1" → FAIL řádek `IS-IS level 1 : nakonfigurován`.
3. Transit: level "2" `passive=False` → PASS passive-absence; `passive=True` → FAIL (pasivní tranzit nesestaví adjacency).
4. Rozhraní chybí ve výpisu → FAIL `chybí v outputu`.
5. Na loopbacku se měří `lo0.*` ze selektorů (ne transit filtr!) — loopback scope selectors.interfaces = ["lo0.0"].

`isis_overview` (jen loopback):
6. `overload_enabled=True` → WARN (Outcome.DEGRADED) `nastaven`; `False` → PASS `nenastaven`.
7. Transit scope → `applies_to` False.

- [ ] **Step 2: FAIL** — Run: `pyats-venv/bin/python -m pytest tests/checks/test_core_protocols.py -v -k "isis_interface or overview"`

- [ ] **Step 3: Implementace**

```python
@register
class IsisInterfaceInfoCheck(Check):
    id = "isis_interface_info"
    title = "IS-IS konfigurace rozhrani"
    label = "IS-IS interface"
    mode = Mode.STATE
    requires = ("isis_interface",)
    requires_inventory = True
    service_types = CORE
    service_subtypes = frozenset({"transit", "loopback"})
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        loopback = ctx.scope.service_subtype == "loopback"
        names = (
            sorted(ctx.scope.selectors.interfaces)
            if loopback
            else _scope_transit_interfaces(ctx)
        )
        data: dict[str, Any] = ctx.subject.get("isis_interface", {})
        findings: list[Finding] = []
        for name in names:
            entry = data.get(name)
            if entry is None:
                findings.append(Finding(
                    Outcome.BROKEN,
                    f"{name}: rozhrani neni v IS-IS interface vypisu",
                    label=qualified(self.label, name), value=MISSING,
                ))
                continue
            levels = entry.get("levels", {})
            findings.append(Finding(
                Outcome.OK if "2" in levels else Outcome.BROKEN,
                f"{name}: IS-IS level 2 {'nakonfigurovan' if '2' in levels else 'chybi'}",
                label=qualified("IS-IS level 2", name),
                value="nakonfigurován" if "2" in levels else MISSING,
            ))
            if "1" in levels:
                findings.append(Finding(
                    Outcome.BROKEN,
                    f"{name}: IS-IS level 1 nema na Core rozhrani co delat",
                    label=qualified("IS-IS level 1", name), value="nakonfigurován",
                ))
            passive = bool(levels.get("2", {}).get("passive"))
            # Loopback pasivni byt musi (nema souseda), transit nesmi
            # (pasivni port nesestavi adjacency, kterou meri
            # isis_adjacency_state).
            ok = passive if loopback else not passive
            findings.append(Finding(
                Outcome.OK if ok else Outcome.BROKEN,
                f"{name}: level 2 passive={'ano' if passive else 'ne'}",
                label=qualified("IS-IS level 2 passive", name),
                value="Passive" if passive else "bez Passive",
            ))
        return findings


@register
class IsisOverviewCheck(Check):
    id = "isis_overview"
    title = "IS-IS overview routeru"
    label = "IS-IS overload bit"
    mode = Mode.STATE
    requires = ("isis_overview",)
    requires_inventory = True
    service_types = CORE
    service_subtypes = frozenset({"loopback"})
    default_severity = Severity.ADVISORY

    def run(self, ctx: CheckContext) -> list[Finding]:
        overview: dict[str, Any] = ctx.subject.get("isis_overview", {})
        overload = bool(overview.get("overload_enabled"))
        return [Finding(
            Outcome.DEGRADED if overload else Outcome.OK,
            "overload bit je nastaveny - router se vyhyba tranzitnimu provozu"
            if overload else "overload bit neni nastaveny",
            value="nastaven" if overload else "nenastaven",
        )]
```

- [ ] **Step 4: PASS + suita** — Run: `pyats-venv/bin/python -m pytest tests/checks/test_core_protocols.py tests/ -x -q`

- [ ] **Step 5: Commit** — `git commit -am "feat: checky isis_interface_info a isis_overview"`

---

### Task 10: Checky `ldp_neighbor_state`, `pim_neighbor_state`, `mpls_interface_state`

**Files:**
- Modify: `migration_validator/checks/core_protocols.py`
- Test: `tests/checks/test_core_protocols.py` (rozšíření)

**Interfaces:**
- Consumes: areas `ldp_neighbor`/`pim_neighbor` (`{neighbor_address, uptime_seconds}`), `mpls_interface` (`{state}`); `ctx.scope.selectors.protocols` (Task 7); `format_uptime` (Task 8).
- Produces: check ids `ldp_neighbor_state`, `pim_neighbor_state`, `mpls_interface_state`.

- [ ] **Step 1: Failing testy**

LDP (vždy očekávaný):
1. Neighbor s `uptime_seconds=25210` → PASS `Up for 7h 0m` + INFO adresa.
2. `uptime_seconds=0`/None → FAIL.
3. Rozhraní bez záznamu → FAIL `LDP neighbor status : Down`.
4. Baseline s jinou adresou → WARN na adresním řádku.

PIM (gate na záměr):
5. `"pim"` v `selectors.protocols` + neighbor s uptime > 0 → PASS + INFO adresa.
6. `"pim"` v protocols + žádný záznam → FAIL `Down`.
7. Bez `"pim"` v protocols → žádné řádky (ne SKIP!) — `run` vrací `[]`.
8. Baseline s jinou adresou → WARN.

MPLS:
9. `state="Up"` → PASS; `state="Dn"` → FAIL `Down`; chybějící záznam → FAIL `chybí v outputu`.

- [ ] **Step 2: FAIL** — Run: `pyats-venv/bin/python -m pytest tests/checks/test_core_protocols.py -v -k "ldp or pim or mpls"`

- [ ] **Step 3: Implementace**

Sdílená kostra pro LDP/PIM (soukromá funkce modulu, ne dědičnost — dva checky se liší jen gate + labely):

```python
def _neighbor_findings(
    ctx: CheckContext, *, area: str, status_label: str, address_label: str,
    names: list[str],
) -> list[Finding]:
    subject: dict[str, Any] = ctx.subject.get(area, {})
    baseline: dict[str, Any] = (ctx.baseline or {}).get(area, {})
    findings: list[Finding] = []
    for name in names:
        entry = subject.get(name)
        was = baseline.get(name)
        if entry is None:
            findings.append(Finding(
                Outcome.BROKEN, f"{name}: soused ve vypisu neni",
                label=qualified(status_label, name), value="Down",
                baseline_value="Up" if was else None,
            ))
            continue
        seconds = entry.get("uptime_seconds")
        up = bool(seconds and seconds > 0)
        findings.append(Finding(
            Outcome.OK if up else Outcome.BROKEN,
            f"{name}: session {'bezi' if up else 'nebezi'}",
            label=qualified(status_label, name),
            value=f"Up for {format_uptime(seconds)}" if up else "Down",
        ))
        address = entry.get("neighbor_address")
        was_address = was.get("neighbor_address") if was else None
        changed = ctx.has_baseline and was is not None and address != was_address
        findings.append(Finding(
            Outcome.DEGRADED if changed else Outcome.INFO,
            f"{name}: adresa souseda {address}",
            label=qualified(address_label, name),
            value=str(address),
            baseline_value=str(was_address) if was_address else None,
        ))
    return findings
```

```python
@register
class LdpNeighborStateCheck(Check):
    id = "ldp_neighbor_state"
    title = "Stav LDP souseda"
    label = "LDP neighbor status"
    mode = Mode.BOTH
    requires = ("ldp_neighbor",)
    requires_inventory = True
    service_types = CORE
    service_subtypes = frozenset({"transit"})
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        # LDP na tranzitnim Core rozhrani je ocekavany vzdy
        # (rozhodnuti 2026-08-26) - zadny gate na zamer.
        return _neighbor_findings(
            ctx, area="ldp_neighbor",
            status_label="LDP neighbor status",
            address_label="LDP neighbor address",
            names=_scope_transit_interfaces(ctx),
        )


@register
class PimNeighborStateCheck(Check):
    id = "pim_neighbor_state"
    title = "Stav PIM souseda"
    label = "PIM neighbor status"
    mode = Mode.BOTH
    requires = ("pim_neighbor",)
    requires_inventory = True
    service_types = CORE
    service_subtypes = frozenset({"transit"})
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        if "pim" not in ctx.scope.selectors.protocols:
            # Bez zameru ticho, ne SKIP - sluzba bez PIM neni mene zdrava
            # (rozhodnuti 2026-08-26).
            return []
        return _neighbor_findings(
            ctx, area="pim_neighbor",
            status_label="PIM neighbor status",
            address_label="PIM neighbor address",
            names=_scope_transit_interfaces(ctx),
        )


@register
class MplsInterfaceStateCheck(Check):
    id = "mpls_interface_state"
    title = "Stav MPLS rozhrani"
    label = "MPLS interface status"
    mode = Mode.BOTH
    requires = ("mpls_interface",)
    requires_inventory = True
    service_types = CORE
    service_subtypes = frozenset({"transit"})
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        subject: dict[str, Any] = ctx.subject.get("mpls_interface", {})
        baseline: dict[str, Any] = (ctx.baseline or {}).get("mpls_interface", {})
        findings: list[Finding] = []
        for name in _scope_transit_interfaces(ctx):
            entry = subject.get(name)
            was = baseline.get(name)
            was_state = str(was.get("state")) if was else None
            if entry is None:
                findings.append(Finding(
                    Outcome.BROKEN,
                    f"{name}: rozhrani neni pod protocols mpls",
                    label=qualified(self.label, name),
                    value=MISSING, baseline_value=was_state,
                ))
                continue
            state = str(entry.get("state", "unknown"))
            findings.append(Finding(
                Outcome.OK if state == "Up" else Outcome.BROKEN,
                f"{name}: MPLS {state}",
                label=qualified(self.label, name),
                value="Up" if state == "Up" else "Down",
                baseline_value=was_state,
            ))
        return findings
```

- [ ] **Step 4: PASS + suita** — Run: `pyats-venv/bin/python -m pytest tests/checks/test_core_protocols.py tests/ -x -q`

- [ ] **Step 5: Commit** — `git commit -am "feat: checky ldp/pim/mpls stavu na Core transitu"`

---

### Task 11: Check `bfd_transit_state`

**Files:**
- Modify: `migration_validator/checks/core_protocols.py`
- Test: `tests/checks/test_core_protocols.py` (rozšíření)

**Interfaces:**
- Consumes: `ctx.subject["bfd"]` (session dicty s `state`, `interface` — do transit scopu je vybral Task 7), `_scope_transit_interfaces`.
- Produces: check id `bfd_transit_state`. `checks/bfd.py` se NEMĚNÍ.

- [ ] **Step 1: Failing testy**

1. Session `{state: "Up", interface: "ge-0/0/0.0"}` → PASS `Up`, label `BFD (ge-0/0/0.0)` s peerem ve zprávě.
2. Session `state: "Down"` → FAIL.
3. Rozhraní bez session → FAIL `Down` (vždy očekávaná, jako LDP).
4. Dvě sessions na jednom rozhraní (IPv4+IPv6 peer) → dva řádky, žádný FAIL za "chybějící".
5. Zákaznický scope → `applies_to` False; `bfd_session_state` (starý check) na transit Core scope neběží — přidej `service_types`/`applies_to` assert, viz Step 3 pozn.

- [ ] **Step 2: FAIL** — Run: `pyats-venv/bin/python -m pytest tests/checks/test_core_protocols.py -v -k bfd_transit`

- [ ] **Step 3: Implementace**

```python
@register
class BfdTransitStateCheck(Check):
    id = "bfd_transit_state"
    title = "Stav BFD na tranzitnim rozhrani"
    label = "BFD"
    mode = Mode.BOTH
    requires = ("bfd",)
    requires_inventory = True
    service_types = CORE
    service_subtypes = frozenset({"transit"})
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        sessions: dict[str, Any] = ctx.subject.get("bfd", {})
        by_interface: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for peer, data in sessions.items():
            by_interface.setdefault(str(data.get("interface", "")), []).append((peer, data))

        findings: list[Finding] = []
        for name in _scope_transit_interfaces(ctx):
            entries = by_interface.get(name)
            if not entries:
                # BFD je na tranzitu ocekavane vzdy (rozhodnuti
                # 2026-08-26) - zadny zamer se neparsuje.
                findings.append(Finding(
                    Outcome.BROKEN,
                    f"{name}: zadna BFD session",
                    label=qualified(self.label, name), value="Down",
                ))
                continue
            for peer, data in sorted(entries):
                state = str(data.get("state", "unknown"))
                findings.append(Finding(
                    Outcome.OK if state == "Up" else Outcome.BROKEN,
                    f"{name}: BFD session s {peer} {state}",
                    label=qualified(self.label, name),
                    value=state.capitalize(), subject=data,
                ))
        return findings
```

Pozn. ke kolizi se starým checkem: `BfdSessionStateCheck` má `service_types = None` (běží všude) — ověř to a pokud ano, dej mu `service_types = CUSTOMER_SERVICE_TYPES | frozenset({"E-Line", "E-LAN"})`? NE — to je změna chování mimo scope. Správně: starý check na transit Core scopu poběží nad sessions vybranými podle rozhraní a vypsal by WARN `bez konfigurace`. Tomu zabraň gate v starém checku:

```python
def applies_to(self, scope):
    if scope.service_type == "Core" and scope.service_subtype == "transit":
        # Transit BFD meri bfd_transit_state; zamerova logika by tu
        # vypisovala WARN 'bez konfigurace' za kazdou session.
        return False
    return super().applies_to(scope)
```

+ test na to (bod 5 v Step 1).

- [ ] **Step 4: PASS + suita** — Run: `pyats-venv/bin/python -m pytest tests/checks/ tests/ -x -q`

- [ ] **Step 5: Commit** — `git commit -am "feat: check bfd_transit_state, stary BFD check mimo Core transit"`

---

### Task 12: Mutanty, dokumentace, re-capture, finální suita

**Files:**
- Modify: `docs/cs/files/checks.md`, `docs/cs/files/collectors.md`, `docs/cs/reference.md` + anglické protějšky v `docs/en/`
- Modify: `runs/mig01/*` (re-capture), kořenové inventory ymls dle potřeby

**Interfaces:**
- Consumes: vše z Tasků 1–11.
- Produces: uzavřená vlna — dokumentace, čerstvé snímky, zelená suita.

- [ ] **Step 1: Mutanty**

Pro každý nový check spusť aspoň jeden ručně provedený mutant a ověř, že testová suita padne (výsledek zapiš do docstringu testu, který mutanta zabíjí — a tvrzení ověř znovu spuštěním, ne z paměti):

- `isis_adjacency_state`: prohoď `Outcome.DEGRADED` → `Outcome.OK` ve větvi „Up teď / Down v baseline".
- `isis_interface_info`: obrať `ok = passive if loopback else not passive` na `ok = passive`.
- `pim_neighbor_state`: smaž gate `if "pim" not in ...`.
- `bfd_transit_state`: smaž větev `if not entries`.
- `Scope.select`: smaž subtype podmínku u `isis_overview` (transit ji dostane) — musí padnout scoping test.

Run po každém mutantu: `pyats-venv/bin/python -m pytest tests/ -x -q` → Expected: aspoň jeden FAIL; pak mutanta vrať.

- [ ] **Step 2: Dokumentace cs + en**

- `checks.md`: sekce pro sedm nových checků (id, service_types+subtype, pravidla PASS/WARN/FAIL vč. sémantiky absence, PIM gate).
- `collectors.md`: šest nových collectorů s RPC a CLI ekvivalenty (`show isis adjacency detail`, `show isis interface detail`, `show isis overview`, `show ldp neighbor detail`, `show pim neighbors`, `show mpls interface`) + poznámka o lo0 filtru u LDP.
- `reference.md`: schema bumpy (inventory 7, snapshot 11), Core subtype, interní BGP tag.
- Anglické verze zrcadlí české.

- [ ] **Step 3: Re-capture runs/mig01**

Na laborce (heslo viz Global Constraints): přegeneruj inventory (parser CLI dle README) a snímky přes `mig-validate record`/`capture` postupem, jakým vznikly stávající `runs/mig01` (viz `runs/` a docs). Ověř: nové snapshoty mají `schema_version: 11`, inventory 7, a `mig-validate evaluate` vyrenderuje Core transit blok s novými řádky a lo0.0 blok s BGP/ISIS řádky. Vlož výstup jednoho bloku do commit message.

- [ ] **Step 4: Finální suita**

Run: `pyats-venv/bin/python -m pytest tests/ -q`
Expected: vše zelené (počet testů vzroste z 1089; 1 skip pokud trvá).

- [ ] **Step 5: Commit + roadmap**

```bash
git add -A
git commit -m "docs+data: dokumentace Core vlny, re-capture runs/mig01 (schema 7/11)"
```

Napiš `docs/superpowers/roadmap-2026-08-26-core-vlna-hotovo.md` ve stylu předchozích roadmap (co vlna přinesla / co vyšlo jinak / co zbývá — bod 21 ESI DF role zůstává otevřený) a commitni.
