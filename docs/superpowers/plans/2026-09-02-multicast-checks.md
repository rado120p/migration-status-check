# Multicast: IGMP, multicast forwarding a MVPN-IGMP checky — implementační plán

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tři nové multicast fact areas (`igmp_group`, `multicast_route`, `mvpn_instance`), dva nové service subtypy (`Internet/multicast`, `IPVPN/mvpn-igmp`), inet.2 statiky na Core lo0.0, a čtyři nové checky (`igmp_membership_report`, `multicast_forwarding_status`, `core_multicast_forwarding`, `mvpn_cmulticast_status`) — pro MX i EVO, fixtures z laborky.

**Architecture:** Stávající vzor jeden collector = jedno RPC (modul `collectors/multicast.py`), checky s `service_types`/`service_subtypes` gate v jednom modulu `checks/multicast.py` (jako `core_protocols.py`), selekce faktů ve `Scope.select()`, záměr z parseru do `ServiceEntry.protocol` / `service_subtype` / nového `l2_interface`. Inventory schema 7→8, snapshot schema 11→12.

**Tech Stack:** Python 3.11+, lxml, junos-eznc (PyEZ), PyYAML, pytest.

**Spec:** `docs/superpowers/specs/2026-09-02-multicast-checks-design.md` — plán z něj argumentuje, implementátor čte oba dokumenty.

## Global Constraints

- Testy: `pyats-venv/bin/python -m pytest tests/ -q` (projektový venv). Před začátkem: suita zelená (1272 testů k 2026-09-01).
- Laborka: `172.20.20.4` = MX (`junos`, hostname `MX1-POP1`), `172.20.20.5` = PTX (`junos-evo`, `PTX1-POP1`). Uživatel `admin`, heslo v env `MIG_LAB_PASSWORD`, které je v `~/.bashrc` **pod** neinteraktivním guardem — načíst přes `eval "$(grep '^export MIG_LAB_PASSWORD=' ~/.bashrc)"` na začátku každého příkazu, který sahá na laborku.
- Hlášky do reportu a hodnoty sloupců: **čeština bez diakritiky** (konvence `checks/`). Komentáře v kódu česky (bez diakritiky, jako sousední moduly), vysvětlují *proč*. Docstringy testů mohou pojmenovat mutanta, kterého test zabíjí — ale jen po jeho skutečném spuštění (Task 11).
- **Stav se nefabuluje:** collector nikdy nesyntetizuje záznam; chybí-li rozhraní/instance ve výpisu, chybí klíč. Co absence znamená, říká check.
- **Ticho vs. SKIP:** bez záměru (žádná inet.2 statika na lo0.0) check nevrací nic; bez IGMP reportu vrací SKIP řádek (kaskáda, rozhodnutí specu).
- **Porovnání proti baseline** jen: množina (S,G) z IGMP, množina (S,G) per inet.2 prefix, sender PE provider tunelu. Nikdy upstream/downstream/rate/uptime.
- Uzavřená rozhodnutí specu se neotvírají (sekce „Uzavřená rozhodnutí").
- Commit po každém Tasku; message styl repa (`feat:`/`fix:`/`test:`/`docs:` + česká věta), zakončení:

```
Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01WivLjHSrouozoY4j92Cw4w
```

- `config/settings.yml` je v pracovním stromu lokálně změněný (nastavení uživatele) — **necommitovat**, používat `git add <konkrétní soubory>`, ne `git add -A`.

---

### Task 1: Lab capture tří nových RPC + re-record rout + ověření tvarů

**Files:**
- Create: `tests/fixtures/rpc/junos/igmp_group.xml`, `multicast_route.xml`, `mvpn_instance.xml`
- Create: totéž pod `tests/fixtures/rpc/junos-evo/`
- Modify (re-record): `tests/fixtures/rpc/{junos,junos-evo}/routes.xml`, `routes.2.xml` — dnešní nahrávky mají `inet.2` jen jako prázdnou hlavičku tabulky, Core check potřebuje změřené `via` inet.2 statiky
- Create (scratch, necommituje se): `<scratchpad>/capture_multicast_rpcs.py`

**Interfaces:**
- Consumes: PyEZ `Device`, `migration_validator.connection.junos.detect_platform(device)`.
- Produces: 6 nových + 4 přepsané fixture soubory; **ověřené PyEZ kwargs** pro `instance all` a `inet`, zapsané do commit message a případně opravené v Tasku 2 (`RPC_KWARGS` níže).

- [ ] **Step 1: Napiš scratch skript**

```python
"""Jednorazovy capture multicast RPC z laborky (spec 2026-09-02). Necommituje se."""
import os, sys
from pathlib import Path
from lxml import etree
from jnpr.junos import Device
from migration_validator.connection.junos import detect_platform

# Kwargs jsou predpoklad specu - Step 2 je overi pres display_xml_rpc.
RPCS = [
    ("get_igmp_group_information", {}, "igmp_group"),
    ("get_multicast_route_information", {"extensive": True, "instance": "all"}, "multicast_route"),
    ("get_mvpn_instance_information", {"inet": True}, "mvpn_instance"),
]
ROUTES = [
    ("get_route_information", {"protocol": "static"}, "routes"),
    ("get_route_information", {"protocol": "aggregate"}, "routes.2"),
]
COMMANDS = (
    "show igmp group",
    "show multicast route instance all extensive",
    "show mvpn instance inet",
)
ROOT = Path("tests/fixtures/rpc")

for host in ("172.20.20.4", "172.20.20.5"):
    with Device(host=host, user="admin", passwd=os.environ["MIG_LAB_PASSWORD"],
                normalize=True) as dev:
        platform = detect_platform(dev)
        print(f"== {host} -> {platform}")
        for command in COMMANDS:
            # Autorita pro kwargs: Junos sam rekne, jak RPC vypada.
            print(f"-- {command}\n{dev.display_xml_rpc(command, format='text')}")
        for rpc_name, kwargs, area in RPCS + ROUTES:
            try:
                reply = getattr(dev.rpc, rpc_name)(**kwargs)
            except Exception as error:
                print(f"  {rpc_name}{kwargs}: SELHALO - {error}", file=sys.stderr)
                continue
            target = ROOT / platform / f"{area}.xml"
            target.write_bytes(etree.tostring(reply, pretty_print=True))
            print(f"  {target}")
```

- [ ] **Step 2: Načti heslo a spusť**

Run: `eval "$(grep '^export MIG_LAB_PASSWORD=' ~/.bashrc)" && pyats-venv/bin/python <scratchpad>/capture_multicast_rpcs.py`
Expected: 10 souborů (5 na platformu) a tři `display_xml_rpc` výpisy na platformu. Porovnej výpis s předpokladem: `<get-multicast-route-information><extensive/><instance>all</instance>` a `<get-mvpn-instance-information><inet/>` (nebo jiný tvar — např. `<family>inet</family>` / `<display-inet/>`). **Pokud se liší, oprav `RPC_KWARGS` v Tasku 2 a poznamenej to do commit message.** Pokud MX RPC selže, je to nález (jiný název RPC na MX → Task 2 dostane per-platform `rpc_name` větev jako `collectors/evpn.py`), ne důvod soubor fabulovat.

- [ ] **Step 3: Ověř tvary proti specu a zapiš je**

Otevři každý soubor a potvrď (zapiš do commit message):
- `igmp_group`: per-rozhraní `mgm-interface-groups` s `interface-name`, `mgm-group` s `multicast-group-address` / `multicast-source-address`; přítomnost pseudo-rozhraní `local`; namespace `junos-routing` (→ iterace `{*}`).
- `multicast_route`: `route-family` > `multicast-instance` + `address-family` + `multicast-route` s `upstream-interface-name`, `downstream-interface-names/interface-name`, `forwarding-rate-packets`, `multicast-route-uptime` s atributem `junos:seconds`; že `instance all` vrací i RI `MULTICAST-STREAM-B-MUX1-RECEIVER` s upstream `lsi.*`/`vt-*`.
- `mvpn_instance`: `instance-entry` > `instance-name`, `c-multicast-ipv4/c-multicast-ipv4-entry` s `c-multicast-address` (`S/32:G/32`) a `provider-tunnel-id` (`RSVP-TE P2MP:<PE>, <id>,<PE>` nebo `I-P-tnl:invalid`).
- `routes.xml`: tabulka `inet.2` má teď `rt` záznam pro statiku (`10.11.11.1/32` s `via` tranzitním rozhraním) — na obou platformách.

- [ ] **Step 4: Spusť suitu — očekávaná červená**

Run: `pyats-venv/bin/python -m pytest tests/collectors/test_conformance.py -q`
Expected: FAIL v testu porovnávajícím sadu fixtures s collectory („osirelo `igmp_group.xml`…") — tři nové soubory zatím nemají collector. Task 2 to uzavírá. Ostatní testy (routes) musí projít i s přepsanými nahrávkami; pokud test rout padne na inet.2 obsahu, je to skutečná regrese k opravě, ne k obejití.

- [ ] **Step 5: Commit fixtures**

```bash
git add tests/fixtures/rpc/junos/*.xml tests/fixtures/rpc/junos-evo/*.xml
git commit -m "test: fixtures igmp_group/multicast_route/mvpn_instance z laborky + re-record rout s inet.2

Overene tvary a kwargs: <sem vloz zjisteni ze Step 2 a 3>"
```

---

### Task 2: Collectory `igmp_group`, `multicast_route`, `mvpn_instance` + snapshot schema 12

**Files:**
- Create: `migration_validator/collectors/multicast.py`
- Modify: `migration_validator/collectors/all.py` (import `multicast`)
- Modify: `migration_validator/models/scope.py:19-37` (`FACT_AREAS` + tři jména)
- Modify: `migration_validator/models/snapshot.py:24-26` (`SCHEMA_VERSION = 12` + komentář `# 12:`)
- Modify: `tests/models/test_snapshot.py` (`test_snapshot_version_is_eleven` → `..._is_twelve`)
- Modify: `tests/collectors/test_conformance.py:60-78` (`COLLECTORS` + tři instance)
- Modify: `tests/conftest.py:38-55` (`COLLECTOR_NAMES` + tři jména) a `_facts_for` (tři prázdné areas ve výsledném dictu)
- Test: `tests/collectors/test_multicast.py`

**Interfaces:**
- Produces fact areas:
  - `igmp_group: dict[str, list[{"source": str | None, "group": str}]]` — klíč jméno rozhraní, `local` zahozeno, `0.0.0.0` → `None`.
  - `multicast_route: dict[str, dict[str, dict]]` — klíč instance (`master` nebo jméno RI), uvnitř klíč `f"{source},{group}"` → `{"upstream_interface": str | None, "downstream_interfaces": list[str], "forwarding_rate_pps": int, "uptime_seconds": int | None, "state": str | None, "forwarding_state": str | None}`. Jen `address-family INET`. Instance bez rout nemá klíč.
  - `mvpn_instance: dict[str, {"c_multicast": list[{"source_prefix": str, "group_prefix": str, "provider_tunnel_id": str | None, "sender_pe": str | None}]}]` — klíč jméno instance; instance ve výpisu bez c-multicast má klíč s prázdným seznamem.
- Produces funkce: `route_key(source: str, group: str) -> str` (`"S,G"`), `parse_sender_pe(tunnel_id: str | None) -> str | None`.
- Consumes: `_localname_text`, `_seconds_attr` z `collectors/isis.py`.

- [ ] **Step 1: Napiš failing testy** — `tests/collectors/test_multicast.py`:

```python
"""Testy multicast collectoru proti nahranemu XML z laborky (spec 2026-09-02).

igmp_group: pseudo-rozhrani `local` neni sluzba a zahazuje se pri parsovani;
zdroj 0.0.0.0 (ASM/Exclude) se uklada jako None, aby check mohl vypsat (*, G).
multicast_route: klic instance (master | RI), uvnitr "S,G"; jen INET rodina.
mvpn_instance: c-multicast per instance + sender_pe z provider-tunnel-id.
"""

from __future__ import annotations

import pytest
from lxml import etree

from migration_validator.collectors.multicast import (
    IgmpGroupCollector,
    MulticastRouteCollector,
    MvpnInstanceCollector,
    parse_sender_pe,
    route_key,
)

PLATFORMS = ("junos", "junos-evo")

# Rozhrani receiveru MULTICAST-STREAM-A (Internet/multicast) na obou boxech.
IGMP_IFACE = {"junos": "ge-0/0/2.11", "junos-evo": "et-0/0/8.11"}
MVPN_RI = "MULTICAST-STREAM-B-MUX1-RECEIVER"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_igmp_group_parses_fixture(rpc_fixture, platform):
    data = IgmpGroupCollector().parse(rpc_fixture(platform, "igmp_group"), platform)
    entries = data[IGMP_IFACE[platform]]
    assert {"source": "10.11.11.1", "group": "232.1.1.1"} in entries


@pytest.mark.parametrize("platform", PLATFORMS)
def test_igmp_group_drops_local_pseudo_interface(rpc_fixture, platform):
    data = IgmpGroupCollector().parse(rpc_fixture(platform, "igmp_group"), platform)
    assert "local" not in data


@pytest.mark.parametrize("platform", PLATFORMS)
def test_multicast_route_parses_master_and_ri(rpc_fixture, platform):
    data = MulticastRouteCollector().parse(rpc_fixture(platform, "multicast_route"), platform)
    master = data["master"][route_key("10.11.11.1", "232.1.1.1")]
    assert master["upstream_interface"]
    assert IGMP_IFACE[platform] in master["downstream_interfaces"]
    assert isinstance(master["forwarding_rate_pps"], int)
    assert isinstance(master["uptime_seconds"], int)
    ri = data[MVPN_RI][route_key("10.12.12.1", "239.1.1.1")]
    assert ri["upstream_interface"].startswith(("lsi.", "vt-"))


@pytest.mark.parametrize("platform", PLATFORMS)
def test_mvpn_instance_parses_fixture(rpc_fixture, platform):
    data = MvpnInstanceCollector().parse(rpc_fixture(platform, "mvpn_instance"), platform)
    entries = data[MVPN_RI]["c_multicast"]
    assert entries[0]["source_prefix"] == "10.12.12.1/32"
    assert entries[0]["group_prefix"] == "239.1.1.1/32"
    assert entries[0]["provider_tunnel_id"].startswith("RSVP-TE P2MP:")
    assert entries[0]["sender_pe"] == "150.0.0.13"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_rpc_names_and_kwargs(platform):
    assert IgmpGroupCollector().rpc_name(platform) == "get_igmp_group_information"
    assert IgmpGroupCollector().rpc_kwargs(platform) == {}
    assert MulticastRouteCollector().rpc_name(platform) == "get_multicast_route_information"
    assert MulticastRouteCollector().rpc_kwargs(platform) == {"extensive": True, "instance": "all"}
    assert MvpnInstanceCollector().rpc_name(platform) == "get_mvpn_instance_information"
    assert MvpnInstanceCollector().rpc_kwargs(platform) == {"inet": True}


# --- Synteticke varianty (XML odvozeny z nahranych fixtures) -------------

EMPTY_IGMP = "<igmp-group-information/>"
EMPTY_MROUTE = "<multicast-route-information/>"
EMPTY_MVPN = "<mvpn-instance-information/>"


def test_empty_outputs_are_empty_dicts():
    assert IgmpGroupCollector().parse(etree.fromstring(EMPTY_IGMP), "junos") == {}
    assert MulticastRouteCollector().parse(etree.fromstring(EMPTY_MROUTE), "junos") == {}
    assert MvpnInstanceCollector().parse(etree.fromstring(EMPTY_MVPN), "junos") == {}


ASM_GROUP = """
<igmp-group-information>
  <mgm-interface-groups>
    <interface-name>irb.2</interface-name>
    <mgm-group-count>1</mgm-group-count>
    <mgm-group>
      <multicast-group-address>239.5.5.5</multicast-group-address>
      <mgm-group-mode-type>Exclude</mgm-group-mode-type>
      <multicast-source-address>0.0.0.0</multicast-source-address>
    </mgm-group>
  </mgm-interface-groups>
  <mgm-interface-groups>
    <interface-name>irb.3</interface-name>
    <mgm-group-count>0</mgm-group-count>
  </mgm-interface-groups>
</igmp-group-information>
"""


def test_asm_source_is_none_and_interface_without_groups_has_no_key():
    data = IgmpGroupCollector().parse(etree.fromstring(ASM_GROUP), "junos")
    assert data == {"irb.2": [{"source": None, "group": "239.5.5.5"}]}


INET6_ONLY = """
<multicast-route-information>
  <route-family>
    <multicast-instance>master</multicast-instance>
    <address-family>INET6</address-family>
    <multicast-route>
      <multicast-group-address>ff3e::1</multicast-group-address>
      <multicast-source-address>2001:db8::1</multicast-source-address>
      <upstream-interface-name>et-0/0/0.0</upstream-interface-name>
    </multicast-route>
  </route-family>
  <route-family>
    <multicast-instance>master</multicast-instance>
    <address-family>INET</address-family>
  </route-family>
</multicast-route-information>
"""


def test_inet6_family_is_ignored_and_empty_instance_has_no_key():
    assert MulticastRouteCollector().parse(etree.fromstring(INET6_ONLY), "junos") == {}


ROUTE_WITHOUT_DOWNSTREAM = """
<multicast-route-information>
  <route-family>
    <multicast-instance>master</multicast-instance>
    <address-family>INET</address-family>
    <multicast-route>
      <multicast-group-address>232.1.1.1</multicast-group-address>
      <multicast-source-address>10.11.11.1</multicast-source-address>
      <upstream-interface-name>et-0/0/0.0</upstream-interface-name>
      <outgoing-interface-count>0</outgoing-interface-count>
      <forwarding-rate-packets>0</forwarding-rate-packets>
      <multicast-route-state>Active</multicast-route-state>
      <multicast-route-forwarding-state>Pruned</multicast-route-forwarding-state>
      <multicast-route-uptime seconds="12">00:00:12</multicast-route-uptime>
    </multicast-route>
  </route-family>
</multicast-route-information>
"""


def test_route_without_downstream_has_empty_list_and_zero_rate():
    data = MulticastRouteCollector().parse(etree.fromstring(ROUTE_WITHOUT_DOWNSTREAM), "junos")
    route = data["master"]["10.11.11.1,232.1.1.1"]
    assert route["downstream_interfaces"] == []
    assert route["forwarding_rate_pps"] == 0
    assert route["uptime_seconds"] == 12
    assert route["forwarding_state"] == "Pruned"


MVPN_WITHOUT_CMULTICAST = """
<mvpn-instance-information>
  <mvpn-instance>
    <instance-family>
      <address-family>INET</address-family>
      <instance-entry>
        <instance-name>EMPTY-RI</instance-name>
        <provider-tunnel>
          <provider-tunnel-id>I-P-tnl:invalid</provider-tunnel-id>
        </provider-tunnel>
      </instance-entry>
    </instance-family>
  </mvpn-instance>
</mvpn-instance-information>
"""


def test_instance_without_cmulticast_keeps_key_with_empty_list():
    """Instance ve vypisu bez c-multicast != instance mimo vypis - check
    je hlasi jinou vetou, takze klic musi zustat."""
    data = MvpnInstanceCollector().parse(etree.fromstring(MVPN_WITHOUT_CMULTICAST), "junos")
    assert data == {"EMPTY-RI": {"c_multicast": []}}


@pytest.mark.parametrize(
    ("tunnel", "expected"),
    [
        ("RSVP-TE P2MP:150.0.0.13, 24209,150.0.0.13", "150.0.0.13"),
        ("I-P-tnl:invalid", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_sender_pe(tunnel, expected):
    assert parse_sender_pe(tunnel) == expected
```

- [ ] **Step 2: Spusť testy — musí padnout na importu**

Run: `pyats-venv/bin/python -m pytest tests/collectors/test_multicast.py -q`
Expected: `ModuleNotFoundError: migration_validator.collectors.multicast`.

- [ ] **Step 3: Napiš collector modul** — `migration_validator/collectors/multicast.py`:

```python
"""Sber multicast stavu: IGMP skupiny, multicast routy, MVPN instance
(spec 2026-09-02). Tri collectory v jednom modulu - sdileji helpery.

Zadny z nich neinterpretuje: `local` u IGMP se zahazuje jen proto, ze to
neni rozhrani (nikdy nemuze byt sluzbou), ne kvuli verdiktu. Absence
rozhrani/instance ve vypisu = absence klice.
"""

from __future__ import annotations

import re
from typing import Any

from lxml import etree

from migration_validator.collectors.base import Collector
from migration_validator.collectors.isis import _localname_text, _seconds_attr
from migration_validator.collectors.registry import register

# Kwargs overene v Tasku 1 pres display_xml_rpc - pri zmene tvaru RPC se
# meni tady, ne v testech.
RPC_KWARGS = {
    "multicast_route": {"extensive": True, "instance": "all"},
    "mvpn_instance": {"inet": True},
}

ASM_SOURCE = "0.0.0.0"
MASTER = "master"

_SENDER_PE = re.compile(r"P2MP:(\d+\.\d+\.\d+\.\d+)")


def route_key(source: str, group: str) -> str:
    """Klic multicast routy - retezec, aby snapshot zustal JSON-safe."""
    return f"{source},{group}"


def parse_sender_pe(tunnel_id: str | None) -> str | None:
    """Sender PE z provider-tunnel-id ('RSVP-TE P2MP:150.0.0.13, 24209,150.0.0.13').

    Porovnava se jen PE adresa - tunnel id uprostred se pri re-signalizaci
    LSP zmeni a neni to zmena sluzby (rozhodnuti 2026-09-02).
    """
    if not tunnel_id or "invalid" in tunnel_id:
        return None
    match = _SENDER_PE.search(tunnel_id)
    return match.group(1) if match else None


def _localname_texts(node: etree._Element, name: str) -> list[str]:
    return [
        text
        for child in node.iter(f"{{*}}{name}")
        if (text := (child.text or "").strip())
    ]


def _int(text: str | None) -> int:
    try:
        return int(text or 0)
    except ValueError:
        return 0


@register
class IgmpGroupCollector(Collector):
    name = "igmp_group"

    def rpc_name(self, platform: str) -> str:
        return "get_igmp_group_information"

    def parse(self, xml: etree._Element, platform: str) -> dict[str, list[dict[str, Any]]]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for iface_node in xml.iter("{*}mgm-interface-groups"):
            interface = _localname_text(iface_node, "interface-name")
            if not interface or interface == "local":
                continue
            entries = []
            for group_node in iface_node.iter("{*}mgm-group"):
                group = _localname_text(group_node, "multicast-group-address")
                source = _localname_text(group_node, "multicast-source-address")
                if not group:
                    continue
                entries.append({
                    "source": None if source in (None, ASM_SOURCE) else source,
                    "group": group,
                })
            # Rozhrani s nulou skupin nema klic - absence je absence.
            if entries:
                groups[interface] = entries
        return groups


@register
class MulticastRouteCollector(Collector):
    name = "multicast_route"

    def rpc_name(self, platform: str) -> str:
        return "get_multicast_route_information"

    def rpc_kwargs(self, platform: str) -> dict[str, Any]:
        return dict(RPC_KWARGS["multicast_route"])

    def parse(self, xml: etree._Element, platform: str) -> dict[str, dict[str, dict[str, Any]]]:
        tables: dict[str, dict[str, dict[str, Any]]] = {}
        for family_node in xml.iter("{*}route-family"):
            if (_localname_text(family_node, "address-family") or "").upper() != "INET":
                continue
            instance = _localname_text(family_node, "multicast-instance") or MASTER
            routes: dict[str, dict[str, Any]] = {}
            for route_node in family_node.iter("{*}multicast-route"):
                group = _localname_text(route_node, "multicast-group-address")
                source = _localname_text(route_node, "multicast-source-address")
                if not group or not source:
                    continue
                downstream = [
                    name
                    for names_node in route_node.iter("{*}downstream-interface-names")
                    for name in _localname_texts(names_node, "interface-name")
                ]
                uptime = next(route_node.iter("{*}multicast-route-uptime"), None)
                routes[route_key(source, group)] = {
                    "upstream_interface": _localname_text(route_node, "upstream-interface-name"),
                    "downstream_interfaces": downstream,
                    "forwarding_rate_pps": _int(
                        _localname_text(route_node, "forwarding-rate-packets")
                    ),
                    "uptime_seconds": _seconds_attr(uptime),
                    "state": _localname_text(route_node, "multicast-route-state"),
                    "forwarding_state": _localname_text(
                        route_node, "multicast-route-forwarding-state"
                    ),
                }
            if routes:
                tables.setdefault(instance, {}).update(routes)
        return tables


@register
class MvpnInstanceCollector(Collector):
    name = "mvpn_instance"

    def rpc_name(self, platform: str) -> str:
        return "get_mvpn_instance_information"

    def rpc_kwargs(self, platform: str) -> dict[str, Any]:
        return dict(RPC_KWARGS["mvpn_instance"])

    def parse(self, xml: etree._Element, platform: str) -> dict[str, dict[str, Any]]:
        instances: dict[str, dict[str, Any]] = {}
        for entry_node in xml.iter("{*}instance-entry"):
            name = _localname_text(entry_node, "instance-name")
            if not name:
                continue
            c_multicast = []
            for c_node in entry_node.iter("{*}c-multicast-ipv4-entry"):
                address = _localname_text(c_node, "c-multicast-address")
                if not address or ":" not in address:
                    continue
                source_prefix, group_prefix = address.split(":", 1)
                tunnel = _localname_text(c_node, "provider-tunnel-id")
                c_multicast.append({
                    "source_prefix": source_prefix,
                    "group_prefix": group_prefix,
                    "provider_tunnel_id": tunnel,
                    "sender_pe": parse_sender_pe(tunnel),
                })
            # Instance ve vypisu bez c-multicast si klic necha - check
            # rozlisuje "instance neni v mvpn vypisu" od "chybi c-multicast".
            instances[name] = {"c_multicast": c_multicast}
        return instances
```

Poznámka pro implementátora: `_localname_text(entry_node, "instance-name")` hledá potomka podle localname v dokumentovém pořadí — `instance-name` je první dítě `instance-entry`, takže vrací správné jméno; `c-multicast-ipv4-entry` žádný `instance-name` nenese.

- [ ] **Step 4: Zaregistruj a zvedni schema**

`collectors/all.py`: přidej `multicast,` do importu (abecedně mezi `mpls` a `nd`).

`models/scope.py` `FACT_AREAS`: přidej na konec `"igmp_group", "multicast_route", "mvpn_instance",`.

`models/snapshot.py`:

```python
# 12: fact areas igmp_group/multicast_route/mvpn_instance (multicast checky,
#     spec 2026-09-02) a selektor l2_interfaces (IRB -> access porty).
SCHEMA_VERSION = 12
```

`tests/models/test_snapshot.py`: přejmenuj `test_snapshot_version_is_eleven` na `test_snapshot_version_is_twelve` a assert `== 12`.

`tests/collectors/test_conformance.py`: import a přidej `IgmpGroupCollector(), MulticastRouteCollector(), MvpnInstanceCollector(),` na konec `COLLECTORS`.

`tests/conftest.py`: do `COLLECTOR_NAMES` přidej `"igmp_group", "multicast_route", "mvpn_instance"`; v `_facts_for` založ `igmp_group = {}`, `multicast_route = {}`, `mvpn_instance = {}` u ostatních lokálních a vrať je ve finálním dictu (`"igmp_group": igmp_group, ...`). Zdravou syntézu doplní Task 6.

- [ ] **Step 5: Spusť testy**

Run: `pyats-venv/bin/python -m pytest tests/collectors tests/models tests/conftest.py tests/test_end_to_end.py -q`
Expected: vše zelené včetně conformance (sada fixtures == collectory). Pak celá suita: `pyats-venv/bin/python -m pytest tests/ -q` → zelená.

- [ ] **Step 6: Commit**

```bash
git add migration_validator/collectors/multicast.py migration_validator/collectors/all.py \
  migration_validator/models/scope.py migration_validator/models/snapshot.py \
  tests/collectors/test_multicast.py tests/collectors/test_conformance.py \
  tests/models/test_snapshot.py tests/conftest.py
git commit -m "feat: collectory igmp_group, multicast_route, mvpn_instance + snapshot schema 12"
```

---

### Task 3: Parser — IGMP záměr a subtypy `Internet/multicast`, `IPVPN/mvpn-igmp`

**Files:**
- Modify: `migration_validator/parsers/core.py` — `parse()` (~406-410), `_detect_service()` (~1517-1534 IPVPN větve, ~1612-1618 Internet větev), nová metoda `_parse_igmp_interfaces`
- Test: `tests/parsers/test_multicast_intent.py`

**Interfaces:**
- Produces: `"igmp"` v `InterfaceService.protocol` pro rozhraní pod `protocols igmp interface X` (globálně i v RI); `service_subtype == "multicast"` (Internet) / `"mvpn-igmp"` (IPVPN). Task 5 dělá schema bump + regeneraci inventory fixtures — **tenhle task schema nezvedá**, fixtures se subtype `None` se dál načtou.
- Consumes: `self.global_protocols_by_interface`, `_collect_protocols`, `RoutingInstance.protocols`.

- [ ] **Step 1: Napiš failing testy** — `tests/parsers/test_multicast_intent.py`:

```python
"""IGMP zamer a multicast subtypy (spec 2026-09-02).

Internet + igmp -> subtype "multicast"; IPVPN + igmp + protocols mvpn v
instanci -> "mvpn-igmp"; IPVPN s igmp bez mvpn zustava None. Oba parsery
se meni v zamku.
"""

from __future__ import annotations

import pytest
from lxml import etree

from migration_validator.parsers.evo import JunosEvoAcxServiceParser
from migration_validator.parsers.mx import JunosServiceParser

PARSERS = (
    pytest.param(JunosEvoAcxServiceParser, id="evo"),
    pytest.param(JunosServiceParser, id="mx"),
)

# Zrcadli lab: et-0/0/8.11 Internet receiver, irb.2 v MVPN VRF, irb.3 v
# obycejne VRF s IGMP (negativni pripad), et-0/0/8.13 Internet bez IGMP.
CONFIG = """
<configuration>
  <interfaces>
    <interface>
      <name>et-0/0/8</name>
      <unit>
        <name>11</name>
        <description>MULTICAST-STREAM-A-MUX1-RECEIVER-1</description>
        <family><inet><address><name>10.111.11.1/24</name></address></inet></family>
      </unit>
      <unit>
        <name>13</name>
        <description>CPE13-NNI</description>
        <family><inet><address><name>152.11.13.1/29</name></address></inet></family>
      </unit>
    </interface>
    <interface>
      <name>irb</name>
      <unit>
        <name>2</name>
        <description>MUX1 receivers POP1</description>
        <family><inet><address><name>10.12.11.254/24</name></address></inet></family>
      </unit>
      <unit>
        <name>3</name>
        <family><inet><address><name>10.13.11.254/24</name></address></inet></family>
      </unit>
    </interface>
  </interfaces>
  <protocols>
    <igmp>
      <interface><name>et-0/0/8.11</name><version>3</version></interface>
      <interface><name>irb.2</name><version>3</version></interface>
    </igmp>
  </protocols>
  <routing-instances>
    <instance>
      <name>MULTICAST-STREAM-B-MUX1-RECEIVER</name>
      <instance-type>vrf</instance-type>
      <interface><name>irb.2</name></interface>
      <route-distinguisher><rd-type>150.0.0.12:2</rd-type></route-distinguisher>
      <vrf-target><community>target:65000:2</community></vrf-target>
      <protocols>
        <mvpn><receiver-site/></mvpn>
        <pim><interface><name>irb.2</name></interface></pim>
      </protocols>
    </instance>
    <instance>
      <name>PLAIN-VRF</name>
      <instance-type>vrf</instance-type>
      <interface><name>irb.3</name></interface>
      <protocols>
        <igmp><interface><name>irb.3</name></interface></igmp>
      </protocols>
    </instance>
  </routing-instances>
</configuration>
"""


def _services(parser_class):
    return {s.interface: s for s in parser_class(etree.fromstring(CONFIG)).parse()}


@pytest.mark.parametrize("parser_class", PARSERS)
def test_internet_with_igmp_gets_multicast_subtype(parser_class):
    service = _services(parser_class)["et-0/0/8.11"]
    assert service.service_type == "Internet"
    assert service.service_subtype == "multicast"
    assert "igmp" in service.protocol


@pytest.mark.parametrize("parser_class", PARSERS)
def test_internet_without_igmp_keeps_none_subtype(parser_class):
    service = _services(parser_class)["et-0/0/8.13"]
    assert service.service_type == "Internet"
    assert service.service_subtype is None
    assert "igmp" not in service.protocol


@pytest.mark.parametrize("parser_class", PARSERS)
def test_ipvpn_with_igmp_and_mvpn_gets_mvpn_igmp_subtype(parser_class):
    service = _services(parser_class)["irb.2"]
    assert service.service_type == "IPVPN"
    assert service.service_subtype == "mvpn-igmp"
    assert "igmp" in service.protocol
    assert "mvpn" in service.protocol


@pytest.mark.parametrize("parser_class", PARSERS)
def test_ipvpn_with_ri_igmp_but_no_mvpn_stays_plain(parser_class):
    """RI-scoped protocols igmp se pocita jako zamer, ale bez mvpn to neni
    MVPN-IGMP sluzba (rozhodnuti 2026-09-02)."""
    service = _services(parser_class)["irb.3"]
    assert service.service_type == "IPVPN"
    assert service.service_subtype is None
    assert "igmp" in service.protocol


@pytest.mark.parametrize("parser_class", PARSERS)
def test_detection_reason_mentions_igmp(parser_class):
    reasons = " ".join(_services(parser_class)["et-0/0/8.11"].detection_reason)
    assert "igmp" in reasons.lower()
```

- [ ] **Step 2: Spusť — musí padnout**

Run: `pyats-venv/bin/python -m pytest tests/parsers/test_multicast_intent.py -q`
Expected: FAIL na `service_subtype is None` / `"igmp" not in protocol`.

- [ ] **Step 3: Implementuj v `parsers/core.py`**

Do `__init__` (vedle `self.global_protocols_by_interface`) přidej `self.igmp_interfaces: set[str] = set()`.

V `parse()` hned za `self._parse_global_protocol_interfaces("pim")` přidej `self._parse_igmp_interfaces()`.

Nová metoda (vedle `_parse_global_protocol_interfaces`):

```python
    def _parse_igmp_interfaces(self) -> None:
        """Rozhraní pod `protocols igmp interface X` — globálně i uvnitř
        routing-instance (IGMP v RI je platná syntaxe, byť lab ho má
        globálně). Záměr říká: tady se čeká IGMP membership report, tohle
        rozhraní je multicast receiver (spec 2026-09-02).

        Zapisuje se per logické rozhraní do `global_protocols_by_interface`
        (stejný kanál jako PIM), takže `_collect_protocols` ho připojí bez
        další větve. RI-scoped varianta se čte přímo tady, ne přes
        `RoutingInstance.protocols` — ta by dala "igmp" každému rozhraní
        instance, ne jen tomu pod `igmp interface`.
        """
        names = all_texts(
            self.config_xml,
            "./protocols/igmp/interface/name/text()"
            " | ./routing-instances/instance/protocols/igmp/interface/name/text()",
        )
        for name in names:
            self.igmp_interfaces.add(name)
            self.global_protocols_by_interface.setdefault(name, set()).add("igmp")
```

V `_detect_service` uprav obě IPVPN větve a Internet větev:

```python
        if instance_type == "vrf":
            reasons.append("Rozhraní je přiřazeno do routing instance typu vrf.")
            return ("IPVPN", self._ipvpn_subtype(interface, instance, reasons), "high", reasons)

        if (
            instance
            and (instance.route_distinguisher or instance.vrf_targets)
            and self._is_layer3(interface)
        ):
            reasons.append(
                "L3 rozhraní je v instanci s route distinguisherem nebo VRF targetem."
            )
            return ("IPVPN", self._ipvpn_subtype(interface, instance, reasons), "high", reasons)
```

```python
        if self._is_layer3(interface):
            reasons.append(
                "Rozhraní má family inet/inet6 nebo IP adresu a není přiřazeno do zákaznické VRF."
            )
            if interface.name in self.igmp_interfaces:
                reasons.append("Rozhraní je pod protocols igmp — multicast receiver.")
                return ("Internet", "multicast", "high", reasons)
            return ("Internet", None, "medium", reasons)
```

Nová pomocná metoda:

```python
    def _ipvpn_subtype(
        self,
        interface: InterfaceConfig,
        instance: RoutingInstance | None,
        reasons: list[str],
    ) -> str | None:
        """`mvpn-igmp` = IGMP záměr na rozhraní + `protocols mvpn` v instanci.
        IGMP bez mvpn zůstává obyčejná IPVPN (rozhodnutí 2026-09-02)."""
        if (
            interface.name in self.igmp_interfaces
            and instance is not None
            and "mvpn" in instance.protocols
        ):
            reasons.append(
                "Rozhraní je pod protocols igmp a instance má protocols mvpn — MVPN-IGMP receiver site."
            )
            return "mvpn-igmp"
        return None
```

- [ ] **Step 4: Spusť testy**

Run: `pyats-venv/bin/python -m pytest tests/parsers -q`
Expected: zelená (nové testy + stávající; `test_pim_intent` beze změny).

Run: `pyats-venv/bin/python -m pytest tests/ -q` → zelená (inventory fixtures mají stále schema 7 a načtou se; subtype se do nich doplní v Tasku 5).

- [ ] **Step 5: Commit**

```bash
git add migration_validator/parsers/core.py tests/parsers/test_multicast_intent.py
git commit -m "feat(parser): IGMP zamer a subtypy Internet/multicast, IPVPN/mvpn-igmp"
```

---

### Task 4: Parser — globální `inet.2` statiky patří Core lo0.0

**Files:**
- Modify: `migration_validator/parsers/core.py:1359-1401` (`_route_matches_service`)
- Test: `tests/parsers/test_static_routes.py` (nové testy na konec souboru)

**Interfaces:**
- Produces: `StaticRoute` s `rib == "inet.2"` (instance `None`) se objeví jen v `static_route` služby Core `lo0.0`; `<RI>.inet.2` zůstává na subnet pravidle.
- Consumes: `rib_instance`, stávající větev agregátů.

- [ ] **Step 1: Napiš failing testy** — přidej na konec `tests/parsers/test_static_routes.py`:

```python
INET2_WITH_CORE = """
<configuration>
  <interfaces>
    <interface>
      <name>et-0/0/0</name>
      <unit>
        <name>0</name>
        <family>
          <inet><address><name>10.1.1.1/30</name></address></inet>
          <iso/>
          <mpls/>
        </family>
      </unit>
    </interface>
    <interface>
      <name>lo0</name>
      <unit>
        <name>0</name>
        <family>
          <inet><address><name>150.0.0.12/32</name></address></inet>
          <iso><address><name>49.0001.1500.0000.0012.00</name></address></iso>
        </family>
      </unit>
    </interface>
    <interface>
      <name>irb</name>
      <unit>
        <name>9</name>
        <family><inet><address><name>10.9.9.1/24</name></address></inet></family>
      </unit>
    </interface>
  </interfaces>
  <routing-options>
    <rib>
      <name>inet.2</name>
      <static>
        <route>
          <name>10.11.11.1/32</name>
          <next-hop>10.1.1.2</next-hop>
        </route>
      </static>
    </rib>
  </routing-options>
  <routing-instances>
    <instance>
      <name>VRF-A</name>
      <instance-type>vrf</instance-type>
      <interface><name>irb.9</name></interface>
      <routing-options>
        <rib>
          <name>VRF-A.inet.2</name>
          <static>
            <route>
              <name>10.99.0.0/16</name>
              <next-hop>10.9.9.2</next-hop>
            </route>
          </static>
        </rib>
      </routing-options>
    </instance>
  </routing-instances>
</configuration>
"""


@pytest.mark.parametrize("parser_cls", PARSERS)
def test_globalni_inet2_statika_patri_lo0_ne_tranzitu(parser_cls):
    """Next-hop 10.1.1.2 lezi v subnetu et-0/0/0.0 - dnesni subnet pravidlo
    by routu dalo tranzitu. Spec 2026-09-02: vsechny globalni inet.2
    statiky patri Core lo0.0 (stejne jako globalni agregaty)."""
    by_iface = {s.interface: s for s in parser_cls(etree.fromstring(INET2_WITH_CORE)).parse()}

    lo0 = [(r["rib"], r["prefix"]) for r in by_iface["lo0.0"].static_route]
    assert ("inet.2", "10.11.11.1/32") in lo0
    assert all(r["rib"] != "inet.2" for r in by_iface["et-0/0/0.0"].static_route)


@pytest.mark.parametrize("parser_cls", PARSERS)
def test_inet2_v_ramci_vrf_zustava_na_subnet_pravidle(parser_cls):
    by_iface = {s.interface: s for s in parser_cls(etree.fromstring(INET2_WITH_CORE)).parse()}

    vrf = [(r["rib"], r["prefix"]) for r in by_iface["irb.9"].static_route]
    assert vrf == [("VRF-A.inet.2", "10.99.0.0/16")]
    assert all(r["rib"] != "VRF-A.inet.2" for r in by_iface["lo0.0"].static_route)
```

- [ ] **Step 2: Spusť — musí padnout**

Run: `pyats-venv/bin/python -m pytest tests/parsers/test_static_routes.py -q -k inet2`
Expected: první test FAIL (inet.2 sedí na `et-0/0/0.0`), druhý PASS.

- [ ] **Step 3: Implementuj** — v `_route_matches_service` hned za kontrolu RI a před větev agregátů:

```python
        if rib_instance(route.rib) != service.routing_instance:
            return False

        if route.rib == "inet.2":
            # Globalni inet.2 (multicast RPF) statiky patri routeru, ne lince,
            # kterou zrovna ukazuje next-hop: core_multicast_forwarding je
            # cte z lo0.0 scopu a upstream porovnava s jejich `via`
            # (rozhodnuti 2026-09-02). <RI>.inet.2 sem nespada -
            # rib_instance("X.inet.2") je "X", ne None.
            return service.service_type == "Core" and service.interface == "lo0.0"

        if route.route_type == "aggregate":
```

Doplň do docstringu metody větu: „Globální `inet.2` statiky se mapují jen na Core lo0.0 bez ohledu na next-hop (spec 2026-09-02)."

- [ ] **Step 4: Spusť testy**

Run: `pyats-venv/bin/python -m pytest tests/parsers tests/checks/test_routes.py -q` (pokud `test_routes.py` neexistuje, `tests/checks -q`)
Expected: zelená.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/parsers/core.py tests/parsers/test_static_routes.py
git commit -m "feat(parser): globalni inet.2 statiky patri Core lo0.0"
```

---

### Task 5: IRB `l2_interface` z globálních bridge-domains/vlans, inventory schema 8, regenerace inventory fixtures

**Files:**
- Modify: `migration_validator/parsers/core.py` — `InterfaceService` (~176-195), `__init__`/`parse()` (nový `self.global_l2_domains`), `_classify_interface` (~1124-1150), `clean_service_dict` (~1903-1926)
- Modify: `migration_validator/models/inventory.py` — `ServiceEntry` (+`l2_interface`), `from_dict`/`to_dict`, `INVENTORY_SCHEMA_VERSION = 8` + docstring
- Modify: `migration_validator/models/scope.py` — `Selectors.l2_interfaces` + `to_dict`
- Modify: `migration_validator/scoping/builder.py:~85` (`l2_interfaces=list(entry.l2_interface)`)
- Modify: `migration_validator/engine.py:122-145` (`_identity` + `"l2_interfaces"`)
- Modify: `migration_validator/reporting/view.py:241-258` (fallback `link_note = "L2: ..."`)
- Modify: `tests/models/test_inventory.py:305` (`..._is_seven` → `..._is_eight`)
- Regenerate: `tests/fixtures/172.20.20.4.yml`, `tests/fixtures/172.20.20.5.yml`, `172.20.20.4.yml`, `172.20.20.5.yml` (kořen), `runs/mig01/inventory_*.yml` (až v Tasku 11 při re-capture)
- Test: `tests/parsers/test_irb_l2_interface.py`, `tests/reporting/test_view.py` (+1 test), `tests/scoping/test_builder.py` (+1 test)

**Interfaces:**
- Produces: `ServiceEntry.l2_interface: list[str]`, `Selectors.l2_interfaces: list[str]`, identity klíč `"l2_interfaces"`, `ServiceView.link_note == "L2: ge-0/0/2.12"` pro IRB bez EVPN linku. Inventory schema **8**.
- Consumes: `_parse_l2_domain_container`, `BridgeDomain.routing_interface`.

- [ ] **Step 1: Napiš failing parser testy** — `tests/parsers/test_irb_l2_interface.py`:

```python
"""IRB nese sve access porty z globalnich bridge-domains (MX) / vlans (EVO)
(spec 2026-09-02). Vazba je routing-interface / l3-interface == jmeno IRB.
Je to inventory, ne multicast logika - plati pro kazdy IRB."""

from __future__ import annotations

import pytest
from lxml import etree

from migration_validator.parsers.evo import JunosEvoAcxServiceParser
from migration_validator.parsers.mx import JunosServiceParser

INTERFACES = """
  <interfaces>
    <interface>
      <name>ge-0/0/2</name>
      <unit>
        <name>12</name>
        <encapsulation>vlan-bridge</encapsulation>
        <vlan-id>12</vlan-id>
      </unit>
    </interface>
    <interface>
      <name>irb</name>
      <unit>
        <name>2</name>
        <description>MUX1 receivers POP1</description>
        <family><inet><address><name>10.12.11.254/24</name></address></inet></family>
      </unit>
    </interface>
  </interfaces>
  <routing-instances>
    <instance>
      <name>MULTICAST-STREAM-B-MUX1-RECEIVER</name>
      <instance-type>vrf</instance-type>
      <interface><name>irb.2</name></interface>
      <protocols><mvpn><receiver-site/></mvpn></protocols>
    </instance>
  </routing-instances>
"""

CONFIG_MX = f"""
<configuration>
  {INTERFACES}
  <bridge-domains>
    <domain>
      <name>BD-MUX1-B</name>
      <domain-type>bridge</domain-type>
      <vlan-id>12</vlan-id>
      <interface><name>ge-0/0/2.12</name></interface>
      <routing-interface>irb.2</routing-interface>
    </domain>
  </bridge-domains>
</configuration>
"""

CONFIG_EVO = f"""
<configuration>
  {INTERFACES}
  <vlans>
    <vlan>
      <name>VL-MUX1-B</name>
      <vlan-id>2</vlan-id>
      <interface><name>ge-0/0/2.12</name></interface>
      <l3-interface>irb.2</l3-interface>
    </vlan>
  </vlans>
</configuration>
"""

CASES = (
    pytest.param(JunosServiceParser, CONFIG_MX, "BD-MUX1-B", "12", id="mx"),
    pytest.param(JunosEvoAcxServiceParser, CONFIG_EVO, "VL-MUX1-B", "2", id="evo"),
)


@pytest.mark.parametrize(("parser_class", "config", "domain", "vlan"), CASES)
def test_irb_carries_its_access_port_and_domain(parser_class, config, domain, vlan):
    by_iface = {s.interface: s for s in parser_class(etree.fromstring(config)).parse()}
    irb = by_iface["irb.2"]
    assert irb.l2_interface == ["ge-0/0/2.12"]
    assert irb.bridge_domain == [domain]
    assert irb.customer_vlan == [vlan]


@pytest.mark.parametrize(("parser_class", "config", "domain", "vlan"), CASES)
def test_irb_without_domain_has_empty_l2_interface(parser_class, config, domain, vlan):
    stripped = config.replace("irb.2</routing-interface>", "irb.7</routing-interface>").replace(
        "irb.2</l3-interface>", "irb.7</l3-interface>"
    )
    by_iface = {s.interface: s for s in parser_class(etree.fromstring(stripped)).parse()}
    assert by_iface["irb.2"].l2_interface == []
```

- [ ] **Step 2: Spusť — musí padnout**

Run: `pyats-venv/bin/python -m pytest tests/parsers/test_irb_l2_interface.py -q`
Expected: `AttributeError: 'InterfaceService' object has no attribute 'l2_interface'`.

- [ ] **Step 3: Parser**

`InterfaceService`: za `l3_interface` přidej

```python
    # Opacny smer nez l3_interface: IRB nese access porty svych
    # bridge-domain/vlan (globalnich i v instanci). Report z toho dela
    # poznamku "L2: ..." v hlavicce IRB bloku (spec 2026-09-02).
    l2_interface: list[str] = field(default_factory=list)
```

`__init__`: `self.global_l2_domains: list[BridgeDomain] = []`.

`parse()` — hned za `self._parse_routing_instances()`:

```python
        # Globalni bridge-domains (MX) / vlans (EVO) - dosud se stahovaly
        # (CONFIG_HIERARCHIES), ale necetly. Potrebuje je vazba IRB -> access
        # port; access port v globalni domene zadnou sluzbu nedostava (mimo
        # rozsah 2026-09-02).
        self.global_l2_domains = [
            domain
            for container_name in ("bridge-domains", "vlans")
            for container in self.config_xml.xpath(f"./{container_name}")
            for domain in self._parse_l2_domain_container(container)
        ]
```

`_classify_interface` — před `return InterfaceService(...)`:

```python
        irb_domains = self._domains_routed_by(interface) if interface.name.startswith("irb.") else []
```

a v konstruktoru:

```python
            bridge_domain=unique(
                [domain.name for domain in bridge_domains]
                + [domain.name for domain in irb_domains]
            ),
            customer_vlan=unique(
                customer_vlans
                + [vlan for domain in irb_domains for vlan in domain.all_vlan_ids]
            ),
            l2_interface=unique(
                [iface for domain in irb_domains for iface in domain.interfaces]
            ),
```

Nová metoda:

```python
    def _domains_routed_by(self, interface: InterfaceConfig) -> list[BridgeDomain]:
        """Bridge-domains / vlans, jejichz routing-interface (MX) nebo
        l3-interface (EVO) je tenhle IRB - globalni i v instancich."""
        candidates = list(self.global_l2_domains)
        for instance in self.routing_instances.values():
            candidates.extend(instance.bridge_domains + instance.vlans)
        return [d for d in candidates if d.routing_interface == interface.name]
```

`clean_service_dict.ordered_keys`: vlož `"l2_interface"` za `"customer_vlan"`.

- [ ] **Step 4: Inventory model, selektory, builder, identity, view**

`models/inventory.py` `ServiceEntry`: za `customer_vlan` přidej `l2_interface: list[str] = field(default_factory=list)`; do `from_dict` `l2_interface=_as_list(data.get("l2_interface")),`; do `to_dict` `"l2_interface": list(self.l2_interface),`. Schema:

```python
# 8: subtypy Internet "multicast" / IPVPN "mvpn-igmp" a pole l2_interface
#    (IRB -> access porty). Subtype je odvozene datum: stara inventory by
#    na pre strane nesla None a parovaci pravidlo description+type+subtype
#    by baseline scope vyradilo z novych checku.
INVENTORY_SCHEMA_VERSION = 8
```

Do docstringu `load_inventory` přidej odstavec „Verze 8 přidala multicast subtypy a `l2_interface`…" ve stylu verze 7.

`tests/models/test_inventory.py`: `test_inventory_schema_version_is_seven` → `test_inventory_schema_version_is_eight`, assert `== 8`, docstring aktualizuj.

`models/scope.py` `Selectors`: za `lag_members` přidej

```python
    # Access porty IRB (spec 2026-09-02) - jen pro poznamku v hlavicce bloku,
    # do vyberu faktu se nepromitaji (L2 port do IRB scopu nepatri).
    l2_interfaces: list[str] = field(default_factory=list)
```

a do `to_dict` `"l2_interfaces": list(self.l2_interfaces),` (`from_dict` bere klíče z `to_dict`, netřeba měnit).

`scoping/builder.py` v `Selectors(...)` service scopu: `l2_interfaces=list(entry.l2_interface),`.

`engine.py` `_identity`: přidej `"l2_interfaces": list(selectors.l2_interfaces),`.

`reporting/view.py` — za blok, který počítá `link_note` z `link`, přidej:

```python
    if link_note is None and identity.get("l2_interfaces"):
        # IRB bez EVPN linku (access port v globalni bridge-domain/vlan) -
        # port nema vlastni blok, tak ho aspon pojmenuje hlavicka L3 bloku.
        link_note = "L2: " + ", ".join(identity["l2_interfaces"])
```

Ověř, kde `identity` v `build_view` vzniká (`identity = scope.identity or {}` nebo podobně, ~řádek 200) — proměnná už existuje, používá se pro `description`.

- [ ] **Step 5: Testy view a builderu**

Do `tests/reporting/test_view.py` (vedle `test_l3_link_note_points_below`):

```python
def test_irb_without_link_gets_l2_note_from_identity():
    scope = _scope([_check("interface_state")])
    scope.identity["l2_interfaces"] = ["ge-0/0/2.12"]
    view = build_view(scope)
    assert view.link_role is None
    assert view.link_note == "L2: ge-0/0/2.12"
```

(Pokud `_scope` helper `identity` nevystavuje jako mutable dict, předej ho parametrem podle toho, jak helper vypadá — cíl je identity s klíčem `l2_interfaces` a bez `link`.)

Do `tests/scoping/test_builder.py`:

```python
def test_l2_interface_protece_do_selektoru():
    entry = ServiceEntry(
        interface="irb.2", service_type="IPVPN", service_subtype="mvpn-igmp",
        routing_instance="MULTICAST-STREAM-B-MUX1-RECEIVER", l2_interface=["ge-0/0/2.12"],
    )
    (scope,) = build_scopes(Inventory(device="x", entries=[entry]))
    assert scope.selectors.l2_interfaces == ["ge-0/0/2.12"]
    assert scope.id == "svc:irb.2:IPVPN"
```

Run: `pyats-venv/bin/python -m pytest tests/parsers/test_irb_l2_interface.py tests/reporting/test_view.py tests/scoping/test_builder.py tests/models -q`
Expected: zelená kromě testů načítajících `tests/fixtures/172.20.20.*.yml` (schema 7 ≠ 8) — ty opraví Step 6.

- [ ] **Step 6: Regeneruj inventory fixtures z laborky**

Parser CLI čte heslo přes `getpass` z TTY, proto scratch skript `<scratchpad>/regen_inventory.py`:

```python
"""Regenerace inventory fixtures novym parserem (schema 8). Necommituje se."""
import os
from pathlib import Path
from jnpr.junos import Device
from migration_validator.parsers.core import create_yaml_data, retrieve_configuration, write_yaml
from migration_validator.parsers.evo import JunosEvoAcxServiceParser
from migration_validator.parsers.mx import JunosServiceParser

TARGETS = {
    "172.20.20.4": JunosServiceParser,
    "172.20.20.5": JunosEvoAcxServiceParser,
}
for host, parser_cls in TARGETS.items():
    with Device(host=host, user="admin", passwd=os.environ["MIG_LAB_PASSWORD"], normalize=True) as dev:
        config_xml = retrieve_configuration(dev, hierarchies=parser_cls.CONFIG_HIERARCHIES)
        services = parser_cls(config_xml).parse()
        data = create_yaml_data(hostname=host, services=services)
        for path in (Path("tests/fixtures") / f"{host}.yml", Path(f"{host}.yml")):
            write_yaml(data=data, output_path=path)
            print(path, len(services))
```

Run: `eval "$(grep '^export MIG_LAB_PASSWORD=' ~/.bashrc)" && pyats-venv/bin/python <scratchpad>/regen_inventory.py`

Ověř v `tests/fixtures/172.20.20.5.yml`: `schema_version: 8`; `et-0/0/8.11` má `service_subtype: multicast` a `protocol` obsahuje `igmp`; `irb.2` má `service_subtype: mvpn-igmp` a `l2_interface: [et-0/0/8.12]`; `lo0.0` má `static_route` s `rib: inet.2`. Analogicky `.4` (`ge-0/0/2.11`, `ge-0/0/2.12`). Pokud lab nemá některou část nakonfigurovanou, zapiš to do commit message — fixtures se **nedopisují ručně**.

`git diff --stat tests/fixtures/*.yml` projdi: kromě očekávaných změn (subtype, igmp, l2_interface, inet.2) nesmí zmizet žádná služba. Pokud zmizela, je to regrese parseru z Tasků 3–5 → oprav před commitem.

- [ ] **Step 7: Celá suita**

Run: `pyats-venv/bin/python -m pytest tests/ -q`
Expected: zelená. Typické opravy, které mohou být potřeba a jsou legitimní: `test_real_inventory_files_produce_expected_scope_counts` (pokud počítá scopy), conformance test nad novým inventory (nové subtype scopy vidí nové areas — ještě bez checků, takže jen selekce, viz Task 6). Cokoli jiného červeného je regrese k analýze, ne k přepsání očekávání.

- [ ] **Step 8: Commit**

```bash
git add migration_validator/parsers/core.py migration_validator/models/inventory.py \
  migration_validator/models/scope.py migration_validator/scoping/builder.py \
  migration_validator/engine.py migration_validator/reporting/view.py \
  tests/parsers/test_irb_l2_interface.py tests/reporting/test_view.py \
  tests/scoping/test_builder.py tests/models/test_inventory.py \
  tests/fixtures/172.20.20.4.yml tests/fixtures/172.20.20.5.yml 172.20.20.4.yml 172.20.20.5.yml
git commit -m "feat: IRB l2_interface z globalnich bridge-domains/vlans, inventory schema 8, regenerace fixtures"
```

---

### Task 6: Scoping — selekce `igmp_group`, `multicast_route`, `mvpn_instance` + zdravá syntéza v conftest

**Files:**
- Modify: `migration_validator/models/scope.py` — `Scope.select()` service větev (~288-307)
- Modify: `tests/conftest.py` — `_facts_for` (zdravé multicast fakty)
- Test: `tests/models/test_scope_multicast.py`

**Interfaces:**
- Produces v `Scope.select()` service větvi: `"igmp_group"` (per rozhraní), `"multicast_route"` (`{instance: table}` s nejvýše jednou instancí: `master` pro scope bez RI, jinak RI; jen Internet/IPVPN/Core-loopback), `"mvpn_instance"` (per RI). Device scope propouští vše (přes `FACT_AREAS`, hotovo v Tasku 2).
- Checky v Tascích 7–10 čtou `ctx.subject["multicast_route"]` přes helper `multicast_table()` (Task 7), který sloučí hodnoty té jediné instance.

- [ ] **Step 1: Napiš failing testy** — `tests/models/test_scope_multicast.py`:

```python
"""Selekce multicast areas (spec 2026-09-02). multicast_route se vybira podle
instance (master | RI), ne podle rozhrani - filtr per (S,G) dela check."""

from migration_validator.models.scope import Scope, ScopeKey, Selectors, device_scope

FACTS = {
    "igmp_group": {
        "et-0/0/8.11": [{"source": "10.11.11.1", "group": "232.1.1.1"}],
        "irb.2": [{"source": "10.12.12.1", "group": "239.1.1.1"}],
    },
    "multicast_route": {
        "master": {"10.11.11.1,232.1.1.1": {"upstream_interface": "et-0/0/0.0"}},
        "MVPN-RI": {"10.12.12.1,239.1.1.1": {"upstream_interface": "lsi.1048576"}},
    },
    "mvpn_instance": {"MVPN-RI": {"c_multicast": []}},
}


def _scope(service_type, subtype, interfaces, instances=()):
    return Scope(
        id=f"svc:x:{service_type}", kind="service",
        key=ScopeKey("x", service_type, subtype),
        selectors=Selectors(interfaces=list(interfaces), routing_instances=list(instances)),
    )


def test_internet_multicast_scope_gets_its_igmp_and_master_table():
    selected = _scope("Internet", "multicast", ["et-0/0/8.11"]).select(FACTS)
    assert set(selected["igmp_group"]) == {"et-0/0/8.11"}
    assert set(selected["multicast_route"]) == {"master"}
    assert selected["mvpn_instance"] == {}


def test_mvpn_scope_gets_ri_table_and_mvpn_instance():
    selected = _scope("IPVPN", "mvpn-igmp", ["irb.2"], ["MVPN-RI"]).select(FACTS)
    assert set(selected["igmp_group"]) == {"irb.2"}
    assert set(selected["multicast_route"]) == {"MVPN-RI"}
    assert set(selected["mvpn_instance"]) == {"MVPN-RI"}


def test_core_loopback_gets_master_table_but_transit_does_not():
    loopback = _scope("Core", "loopback", ["lo0.0"]).select(FACTS)
    transit = _scope("Core", "transit", ["et-0/0/0.0"]).select(FACTS)
    assert set(loopback["multicast_route"]) == {"master"}
    assert transit["multicast_route"] == {}


def test_elan_scope_sees_no_multicast_area():
    selected = _scope("E-LAN", "vlan-aware", ["ae0.15"], ["EVPN-RI"]).select(FACTS)
    assert selected["igmp_group"] == {}
    assert selected["multicast_route"] == {}
    assert selected["mvpn_instance"] == {}


def test_missing_instance_table_yields_empty_dict_not_key_error():
    selected = _scope("IPVPN", "mvpn-igmp", ["irb.5"], ["OTHER-RI"]).select(FACTS)
    assert selected["multicast_route"] == {}


def test_device_scope_passes_everything():
    selected = device_scope().select(FACTS)
    assert selected["multicast_route"] == FACTS["multicast_route"]
    assert selected["mvpn_instance"] == FACTS["mvpn_instance"]
```

- [ ] **Step 2: Spusť — musí padnout**

Run: `pyats-venv/bin/python -m pytest tests/models/test_scope_multicast.py -q`
Expected: `KeyError: 'igmp_group'` v service větvi.

- [ ] **Step 3: Implementuj v `Scope.select()`** — před `return {` service větve:

```python
        igmp_group = {
            name: data
            for name, data in (facts.get("igmp_group") or {}).items()
            if self.selectors.matches_interface(name)
        }
        # Multicast tabulka patri instanci, ne lince: scope bez RI dostane
        # master, scope s RI svou tabulku. Filtr per (S,G) dela check
        # (IGMP mnozina / inet.2 prefixy). Jen role, ktere multicast meri -
        # tranzitni Core ani L2 sluzby tabulku nedostanou (spec 2026-09-02).
        multicast_route: dict[str, Any] = {}
        measures_multicast = self.service_type in ("Internet", "IPVPN") or (
            self.service_type == "Core" and self.service_subtype == "loopback"
        )
        if measures_multicast:
            instance = (
                self.selectors.routing_instances[0]
                if self.selectors.routing_instances
                else "master"
            )
            table = (facts.get("multicast_route") or {}).get(instance)
            if table:
                multicast_route = {instance: table}
        mvpn_instance = {
            name: data
            for name, data in (facts.get("mvpn_instance") or {}).items()
            if name in self.selectors.routing_instances
        }
```

a do vraceného dictu: `"igmp_group": igmp_group, "multicast_route": multicast_route, "mvpn_instance": mvpn_instance,`.

- [ ] **Step 4: Zdravá syntéza v `tests/conftest.py` `_facts_for`**

Uvnitř smyčky `for scope in scopes:` (po bloku Core transit) přidej:

```python
        # Multicast (spec 2026-09-02): zdravy receiver posila IGMP report,
        # stream tece na servisni rozhrani, upstream odpovida roli. Bez
        # toho by nove checky hlasily FAIL na kazde zdrave migraci (AR-29).
        if scope.service_subtype in ("multicast", "mvpn-igmp") and scope.selectors.interfaces:
            iface = scope.selectors.interfaces[0]
            instance = (
                scope.selectors.routing_instances[0]
                if scope.selectors.routing_instances
                else "master"
            )
            source, group = "10.200.0.1", "232.200.0.1"
            igmp_group[iface] = [{"source": source, "group": group}]
            upstream = "lsi.1048576" if scope.service_subtype == "mvpn-igmp" else "et-0/0/0.0"
            multicast_route.setdefault(instance, {})[f"{source},{group}"] = {
                "upstream_interface": upstream,
                "downstream_interfaces": [iface],
                "forwarding_rate_pps": 6,
                "uptime_seconds": 3266,
                "state": "Active",
                "forwarding_state": "Forwarding",
            }
            if scope.service_subtype == "mvpn-igmp":
                tunnel = "RSVP-TE P2MP:150.0.0.13, 24209,150.0.0.13"
                mvpn_instance[instance] = {"c_multicast": [{
                    "source_prefix": f"{source}/32",
                    "group_prefix": f"{group}/32",
                    "provider_tunnel_id": tunnel,
                    "sender_pe": "150.0.0.13",
                }]}

        # Core lo0.0: ke kazde inet.2 statice existuje stream se zdrojem
        # uvnitr prefixu a upstream == via z route zrcadla vyse (ten je
        # scope.selectors.interfaces[0], tedy "lo0.0" - synteticky, ale
        # konzistentni s tim, co check porovnava).
        if scope.key.service_type == "Core" and scope.service_subtype == "loopback":
            for route in scope.selectors.static_routes:
                if str(route.get("rib")) != "inet.2":
                    continue
                network = ipaddress.ip_network(str(route["prefix"]), strict=False)
                source = str(next(network.hosts(), network.network_address))
                multicast_route.setdefault("master", {})[f"{source},232.100.0.1"] = {
                    "upstream_interface": scope.selectors.interfaces[0],
                    "downstream_interfaces": ["et-0/0/8.11"],
                    "forwarding_rate_pps": 6,
                    "uptime_seconds": 3266,
                    "state": "Active",
                    "forwarding_state": "Forwarding",
                }
```

(`ipaddress` je v conftest už importovaný — používá ho IS-IS blok.) Zkontroluj, že route zrcadlo výše (`for route in scope.selectors.static_routes`) skutečně píše `"via": list(scope.selectors.interfaces[:1])` — Core check porovnává upstream právě s tímhle `via`.

- [ ] **Step 5: Spusť testy**

Run: `pyats-venv/bin/python -m pytest tests/models tests/test_end_to_end.py tests/collectors/test_conformance.py -q`
Expected: zelená. Pak `pyats-venv/bin/python -m pytest tests/ -q` → zelená.

- [ ] **Step 6: Commit**

```bash
git add migration_validator/models/scope.py tests/models/test_scope_multicast.py tests/conftest.py
git commit -m "feat(scope): selekce igmp_group/multicast_route/mvpn_instance podle rozhrani a instance"
```

---

### Task 7: Modul `checks/multicast.py` — helpery + check `igmp_membership_report`

**Files:**
- Create: `migration_validator/checks/multicast.py`
- Modify: `migration_validator/checks/all.py` (import `multicast`)
- Test: `tests/checks/test_multicast.py`

**Interfaces:**
- Produces (helpery, používají Tasky 8–10):
  - `MULTICAST_TYPES = frozenset({"Internet", "IPVPN"})`, `MULTICAST_SUBTYPES = frozenset({"multicast", "mvpn-igmp"})`
  - `NO_REPORT = "Receiver neposila zadny IGMP membership report"`, `NO_REPORT_SKIP = "bez IGMP reportu"`
  - `igmp_pairs(facts: dict | None, scope: Scope) -> list[tuple[str | None, str]]` — seřazené unikátní (source, group) pro rozhraní scopu
  - `sg_label(source: str | None, group: str) -> str` → `"(10.11.11.1, 232.1.1.1)"` / `"(*, 239.1.1.1)"`
  - `pairs_text(pairs) -> str` — labely spojené `", "`
  - `multicast_table(facts: dict | None) -> dict[str, dict]` — sloučí tabulky instancí z `facts["multicast_route"]` (scope jich má nejvýš jednu)
  - `routes_for(table, source, group) -> list[tuple[str, dict]]` — přesný klíč pro S,G; pro `source None` všechny routy s danou group
  - `format_uptime_hms(seconds: int | None) -> str` → `"00:54:26"`, `"1d 02:03:04"`, `"-"` pro None
  - `stream_rows(sg: str, route: dict, *, rate_label: str) -> list[Finding]` — řádky `rate_label` (OK/BROKEN podle `> 0`, value `"{n} pps"`) a `Route uptime` (INFO), oba `group=sg`
- Produces check `igmp_membership_report` (`Mode.BOTH`, `requires=("igmp_group",)`, CRITICAL, gate `MULTICAST_TYPES`/`MULTICAST_SUBTYPES`).
- Consumes: `Check`, `CheckContext`, `Finding`, `Outcome`, `Severity`, `register`.

- [ ] **Step 1: Napiš failing testy** — `tests/checks/test_multicast.py` (základ, další tasky přidávají):

```python
"""Checky multicastu (spec 2026-09-02). Idiom: realny Scope, CheckContext,
primy run(). Baseline scope nese jina jmena rozhrani (ge- vs et-), proto
se IGMP mnozina baseline cte pres ctx.baseline_scope."""

from __future__ import annotations

from migration_validator.checks.base import CheckContext
from migration_validator.checks.multicast import (
    NO_REPORT,
    IgmpMembershipReportCheck,
    format_uptime_hms,
    igmp_pairs,
    routes_for,
    sg_label,
)
from migration_validator.config import default_config
from migration_validator.models.result import Outcome
from migration_validator.models.scope import Scope, ScopeKey, Selectors

POST = "et-0/0/8.11"
PRE = "ge-0/0/2.11"
SG = ("10.11.11.1", "232.1.1.1")


def _scope(interface=POST, service_type="Internet", subtype="multicast", instances=(),
           static_routes=()):
    return Scope(
        id=f"svc:x:{service_type}", kind="service",
        key=ScopeKey("x", service_type, subtype),
        selectors=Selectors(
            interfaces=[interface], routing_instances=list(instances),
            static_routes=[dict(r) for r in static_routes],
        ),
    )


def _ctx(subject, baseline=None, scope=None, baseline_scope=None):
    return CheckContext(
        scope=scope or _scope(), subject=subject, baseline=baseline,
        config=default_config(), baseline_scope=baseline_scope,
    )


def _igmp(iface, *pairs):
    return {"igmp_group": {iface: [{"source": s, "group": g} for s, g in pairs]}}


# --- helpery --------------------------------------------------------------

def test_sg_label_and_asm():
    assert sg_label("10.11.11.1", "232.1.1.1") == "(10.11.11.1, 232.1.1.1)"
    assert sg_label(None, "239.1.1.1") == "(*, 239.1.1.1)"


def test_igmp_pairs_is_sorted_unique_and_scoped():
    facts = {"igmp_group": {
        POST: [{"source": "10.0.0.2", "group": "232.0.0.2"},
               {"source": "10.0.0.1", "group": "232.0.0.1"},
               {"source": "10.0.0.1", "group": "232.0.0.1"}],
        "irb.9": [{"source": "1.1.1.1", "group": "239.9.9.9"}],
    }}
    assert igmp_pairs(facts, _scope()) == [("10.0.0.1", "232.0.0.1"), ("10.0.0.2", "232.0.0.2")]
    assert igmp_pairs(None, _scope()) == []


def test_routes_for_exact_and_asm():
    table = {"10.0.0.1,232.0.0.1": {"a": 1}, "10.0.0.2,232.0.0.1": {"b": 2}}
    assert routes_for(table, "10.0.0.1", "232.0.0.1") == [("10.0.0.1,232.0.0.1", {"a": 1})]
    assert [k for k, _ in routes_for(table, None, "232.0.0.1")] == [
        "10.0.0.1,232.0.0.1", "10.0.0.2,232.0.0.1"]
    assert routes_for(table, "9.9.9.9", "232.0.0.1") == []


def test_format_uptime_hms():
    assert format_uptime_hms(3266) == "00:54:26"
    assert format_uptime_hms(93784) == "1d 02:03:04"
    assert format_uptime_hms(None) == "-"


# --- igmp_membership_report -----------------------------------------------

def test_igmp_report_pass_lists_pairs():
    (row,) = IgmpMembershipReportCheck().run(_ctx(_igmp(POST, SG)))
    assert row.outcome is Outcome.OK
    assert row.label == "IGMP membership report"
    assert row.value == "(10.11.11.1, 232.1.1.1)"


def test_igmp_report_missing_is_fail():
    (row,) = IgmpMembershipReportCheck().run(_ctx({"igmp_group": {}}))
    assert row.outcome is Outcome.BROKEN
    assert row.value == NO_REPORT


def test_igmp_report_same_set_against_baseline_is_pass():
    (row,) = IgmpMembershipReportCheck().run(_ctx(
        _igmp(POST, SG), baseline=_igmp(PRE, SG),
        baseline_scope=_scope(PRE),
    ))
    assert row.outcome is Outcome.OK
    assert row.baseline_value == "(10.11.11.1, 232.1.1.1)"


def test_igmp_report_changed_set_is_warn():
    """Mutant: nahrazeni DEGRADED -> OK ve vetvi 'mnozina se lisi' tenhle
    test polozi (overit spustenim v Tasku 11)."""
    (row,) = IgmpMembershipReportCheck().run(_ctx(
        _igmp(POST, SG), baseline=_igmp(PRE, SG, ("10.11.11.2", "232.1.1.2")),
        baseline_scope=_scope(PRE),
    ))
    assert row.outcome is Outcome.DEGRADED
    assert row.baseline_value == "(10.11.11.1, 232.1.1.1), (10.11.11.2, 232.1.1.2)"


def test_igmp_report_baseline_without_groups_is_not_compared():
    (row,) = IgmpMembershipReportCheck().run(_ctx(
        _igmp(POST, SG), baseline={"igmp_group": {}}, baseline_scope=_scope(PRE),
    ))
    assert row.outcome is Outcome.OK
    assert row.baseline_value is None


def test_igmp_check_applies_only_to_multicast_subtypes():
    check = IgmpMembershipReportCheck()
    assert check.applies_to(_scope())
    assert check.applies_to(_scope("irb.2", "IPVPN", "mvpn-igmp", ["RI"]))
    assert not check.applies_to(_scope("et-0/0/8.13", "Internet", None))
    assert not check.applies_to(_scope("irb.3", "IPVPN", None, ["RI"]))
    assert not check.applies_to(_scope("lo0.0", "Core", "loopback"))
```

- [ ] **Step 2: Spusť — musí padnout**

Run: `pyats-venv/bin/python -m pytest tests/checks/test_multicast.py -q`
Expected: `ModuleNotFoundError: migration_validator.checks.multicast`.

- [ ] **Step 3: Napiš modul** — `migration_validator/checks/multicast.py`:

```python
"""Multicast checky (spec 2026-09-02): IGMP membership report, multicast
forwarding (IGMP-rizene pro Internet/IPVPN, inet.2-rizene pro Core lo0.0)
a MVPN c-multicast / provider tunnel.

IGMP mnozina definuje ocekavane streamy. Bez ni forwarding i MVPN radky
kaskaduji do SKIP (ne nezavisle hledani v tabulce). Upstream, downstream,
rate a uptime se proti baseline neporovnavaji nikdy - meni se migraci
z definice; porovnava se jen mnozina (S,G) a sender PE tunelu.
"""

from __future__ import annotations

import ipaddress
from typing import Any

from migration_validator.checks.base import Check, CheckContext, Mode
from migration_validator.checks.registry import register
from migration_validator.collectors.multicast import route_key
from migration_validator.models.result import Finding, Outcome, Severity
from migration_validator.models.scope import Scope

MULTICAST_TYPES = frozenset({"Internet", "IPVPN"})
MULTICAST_SUBTYPES = frozenset({"multicast", "mvpn-igmp"})

NO_REPORT = "Receiver neposila zadny IGMP membership report"
NO_REPORT_SKIP = "bez IGMP reportu"

# Upstream pravidla per role (rozhodnuti 2026-09-02).
INTERNET_UPSTREAM_PREFIXES = ("ge-", "xe-", "et-", "ae")
MVPN_UPSTREAM_PREFIXES = ("lsi.", "vt-")


# --- helpery ---------------------------------------------------------------

def sg_label(source: str | None, group: str) -> str:
    return f"({source or '*'}, {group})"


def pairs_text(pairs: list[tuple[str | None, str]]) -> str:
    return ", ".join(sg_label(s, g) for s, g in pairs)


def igmp_pairs(facts: dict[str, Any] | None, scope: Scope) -> list[tuple[str | None, str]]:
    """(source, group) pro rozhrani scopu - serazene, bez duplicit.
    Baseline se cte s baseline scopem: jmena rozhrani se migraci meni."""
    groups = (facts or {}).get("igmp_group") or {}
    pairs = {
        (entry.get("source"), str(entry["group"]))
        for name in scope.selectors.interfaces
        for entry in groups.get(name, [])
        if entry.get("group")
    }
    return sorted(pairs, key=lambda p: (p[0] or "", p[1]))


def multicast_table(facts: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Scope ma nejvys jednu instanci (Scope.select), tak se tabulky slouci."""
    table: dict[str, dict[str, Any]] = {}
    for routes in ((facts or {}).get("multicast_route") or {}).values():
        table.update(routes)
    return table


def routes_for(
    table: dict[str, dict[str, Any]], source: str | None, group: str
) -> list[tuple[str, dict[str, Any]]]:
    """Routy k IGMP zaznamu. Multicast tabulka je vzdy (S,G); pro ASM
    zaznam (*, G) patri kazda routa s tou group."""
    if source is not None:
        key = route_key(source, group)
        return [(key, table[key])] if key in table else []
    return sorted(
        (key, route) for key, route in table.items() if key.split(",", 1)[1] == group
    )


def format_uptime_hms(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    days, rest = divmod(int(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    clock = f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{days}d {clock}" if days else clock


def stream_rows(sg: str, route: dict[str, Any], *, rate_label: str) -> list[Finding]:
    """Forwarding rate (> 0) a uptime (INFO) - spolecne pro vsechny tri
    forwarding checky. Bez porovnani proti baseline."""
    pps = int(route.get("forwarding_rate_pps") or 0)
    return [
        Finding(
            Outcome.OK if pps > 0 else Outcome.BROKEN,
            f"{sg}: forwarding rate {pps} pps",
            label=rate_label, group=sg, value=f"{pps} pps",
        ),
        Finding(
            Outcome.INFO,
            f"{sg}: route uptime {format_uptime_hms(route.get('uptime_seconds'))}",
            label="Route uptime", group=sg,
            value=format_uptime_hms(route.get("uptime_seconds")),
        ),
    ]


def _baseline_pairs(ctx: CheckContext) -> list[tuple[str | None, str]] | None:
    if not ctx.has_baseline:
        return None
    return igmp_pairs(ctx.baseline, ctx.baseline_scope or ctx.scope)


# --- igmp_membership_report -------------------------------------------------

@register
class IgmpMembershipReportCheck(Check):
    id = "igmp_membership_report"
    title = "IGMP membership report receiveru"
    label = "IGMP membership report"
    mode = Mode.BOTH
    requires = ("igmp_group",)
    requires_inventory = True
    service_types = MULTICAST_TYPES
    service_subtypes = MULTICAST_SUBTYPES
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        now = igmp_pairs(ctx.subject, ctx.scope)
        was = _baseline_pairs(ctx)
        # Baseline bez skupin = neni s cim porovnat (no-baseline pravidlo),
        # ne "bylo prazdno".
        was_value = pairs_text(was) if was else None
        if not now:
            return [Finding(
                Outcome.BROKEN, "receiver neposila zadny IGMP membership report",
                value=NO_REPORT, baseline_value=was_value,
            )]
        if was and set(was) != set(now):
            return [Finding(
                Outcome.DEGRADED,
                f"IGMP skupiny se zmenily proti baseline: {pairs_text(now)}",
                value=pairs_text(now), baseline_value=was_value,
            )]
        return [Finding(
            Outcome.OK, f"IGMP membership report: {pairs_text(now)}",
            value=pairs_text(now), baseline_value=was_value,
        )]
```

`checks/all.py`: přidej `multicast,` do importu (abecedně za `ifaces`).

- [ ] **Step 4: Spusť testy**

Run: `pyats-venv/bin/python -m pytest tests/checks/test_multicast.py tests/test_end_to_end.py tests/test_cli.py -q`
Expected: zelená (syntéza z Tasku 6 drží AR-29; `checks` CLI výpis obsahuje nový check — pokud nějaký test počítá registrované checky, aktualizuj počet).

- [ ] **Step 5: Commit**

```bash
git add migration_validator/checks/multicast.py migration_validator/checks/all.py tests/checks/test_multicast.py
git commit -m "feat(checks): igmp_membership_report + sdilene multicast helpery"
```

---

### Task 8: Check `multicast_forwarding_status` (Internet/multicast, IPVPN/mvpn-igmp)

**Files:**
- Modify: `migration_validator/checks/multicast.py`
- Test: `tests/checks/test_multicast.py` (přidat)

**Interfaces:**
- Produces check `multicast_forwarding_status`: `Mode.STATE`, `requires=("igmp_group", "multicast_route")`, CRITICAL, stejný gate jako IGMP check. Řádky: souhrn `Multicast forwarding status`, per stream skupina `sg_label` s řádky `Stream`, `Upstream interface`, `Forwarding-rate`, `Route uptime`.
- Consumes: helpery z Tasku 7.

- [ ] **Step 1: Napiš failing testy** — přidej do `tests/checks/test_multicast.py`:

```python
from migration_validator.checks.multicast import MulticastForwardingStatusCheck, NO_REPORT_SKIP


def _route(upstream="et-0/0/0.0", downstream=(POST,), pps=6, uptime=3266):
    return {
        "upstream_interface": upstream, "downstream_interfaces": list(downstream),
        "forwarding_rate_pps": pps, "uptime_seconds": uptime,
        "state": "Active", "forwarding_state": "Forwarding",
    }


def _facts(iface=POST, instance="master", pairs=(SG,), routes=None):
    facts = _igmp(iface, *pairs)
    facts["multicast_route"] = {instance: routes if routes is not None else {
        f"{s},{g}": _route() for s, g in pairs}}
    return facts


def _by_label(findings, group):
    return {f.label: f for f in findings if f.group == group}


def test_forwarding_pass_block_shape():
    findings = MulticastForwardingStatusCheck().run(_ctx(_facts()))
    summary = findings[0]
    assert summary.outcome is Outcome.OK
    assert summary.label == "Multicast forwarding status"
    assert summary.value == "1 S,G"
    assert summary.group is None
    sg = sg_label(*SG)
    rows = _by_label(findings, sg)
    assert [f.label for f in findings[1:]] == ["Stream", "Upstream interface", "Forwarding-rate", "Route uptime"]
    assert rows["Stream"].outcome is Outcome.OK
    assert rows["Stream"].value == f"Stream se na {POST} posila"
    assert rows["Upstream interface"].value == "et-0/0/0.0"
    assert rows["Forwarding-rate"].value == "6 pps"
    assert rows["Route uptime"].outcome is Outcome.INFO
    assert rows["Route uptime"].value == "00:54:26"


def test_forwarding_without_igmp_is_single_skip():
    findings = MulticastForwardingStatusCheck().run(_ctx(
        {"igmp_group": {}, "multicast_route": {"master": {f"{SG[0]},{SG[1]}": _route()}}}))
    assert [(f.outcome, f.value) for f in findings] == [(Outcome.SKIP, NO_REPORT_SKIP)]


def test_forwarding_sg_missing_from_table():
    findings = MulticastForwardingStatusCheck().run(_ctx(_facts(routes={})))
    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "1/1 S,G nefunguje"
    assert len(findings) == 2
    assert findings[1].label == "Stream"
    assert findings[1].value == "S,G neni v multicast tabulce"


def test_forwarding_downstream_without_service_interface_fails_stream_row():
    routes = {f"{SG[0]},{SG[1]}": _route(downstream=["et-0/0/8.99"])}
    findings = MulticastForwardingStatusCheck().run(_ctx(_facts(routes=routes)))
    rows = _by_label(findings, sg_label(*SG))
    assert rows["Stream"].outcome is Outcome.BROKEN
    assert rows["Stream"].value == f"S,G je v tabulce ale stream se na {POST} neposila"
    assert findings[0].outcome is Outcome.BROKEN


def test_forwarding_internet_upstream_must_be_transit():
    routes = {f"{SG[0]},{SG[1]}": _route(upstream="lsi.1048576")}
    findings = MulticastForwardingStatusCheck().run(_ctx(_facts(routes=routes)))
    row = _by_label(findings, sg_label(*SG))["Upstream interface"]
    assert row.outcome is Outcome.BROKEN
    assert row.value == "lsi.1048576"
    assert "nema upstream interface" in row.message


def test_forwarding_mvpn_upstream_must_be_lsi_or_vt():
    scope = _scope("irb.2", "IPVPN", "mvpn-igmp", ["RI"])
    ok = {"10.12.12.1,239.1.1.1": _route(upstream="lsi.1048576", downstream=["irb.2"])}
    bad = {"10.12.12.1,239.1.1.1": _route(upstream="et-0/0/0.0", downstream=["irb.2"])}
    pair = (("10.12.12.1", "239.1.1.1"),)
    good = MulticastForwardingStatusCheck().run(_ctx(_facts("irb.2", "RI", pair, ok), scope=scope))
    wrong = MulticastForwardingStatusCheck().run(_ctx(_facts("irb.2", "RI", pair, bad), scope=scope))
    assert _by_label(good, "(10.12.12.1, 239.1.1.1)")["Upstream interface"].outcome is Outcome.OK
    assert _by_label(wrong, "(10.12.12.1, 239.1.1.1)")["Upstream interface"].outcome is Outcome.BROKEN


def test_forwarding_missing_upstream_renders_dash():
    routes = {f"{SG[0]},{SG[1]}": _route(upstream=None)}
    findings = MulticastForwardingStatusCheck().run(_ctx(_facts(routes=routes)))
    row = _by_label(findings, sg_label(*SG))["Upstream interface"]
    assert row.outcome is Outcome.BROKEN and row.value == "-"


def test_forwarding_zero_rate_fails_rate_row_and_summary():
    routes = {f"{SG[0]},{SG[1]}": _route(pps=0)}
    findings = MulticastForwardingStatusCheck().run(_ctx(_facts(routes=routes)))
    assert _by_label(findings, sg_label(*SG))["Forwarding-rate"].outcome is Outcome.BROKEN
    assert findings[0].value == "1/1 S,G nefunguje"


def test_forwarding_asm_group_matches_every_source():
    routes = {"10.0.0.1,239.5.5.5": _route(), "10.0.0.2,239.5.5.5": _route()}
    findings = MulticastForwardingStatusCheck().run(_ctx(_facts(pairs=((None, "239.5.5.5"),), routes=routes)))
    assert findings[0].value == "1 S,G"
    assert {f.group for f in findings[1:]} == {"(10.0.0.1, 239.5.5.5)", "(10.0.0.2, 239.5.5.5)"}


def test_forwarding_two_streams_one_broken():
    second = ("10.11.11.2", "232.1.1.2")
    routes = {f"{SG[0]},{SG[1]}": _route(), f"{second[0]},{second[1]}": _route(downstream=[])}
    findings = MulticastForwardingStatusCheck().run(_ctx(_facts(pairs=(SG, second), routes=routes)))
    assert findings[0].value == "1/2 S,G nefunguje"
```

- [ ] **Step 2: Spusť — musí padnout**

Run: `pyats-venv/bin/python -m pytest tests/checks/test_multicast.py -q -k forwarding`
Expected: `ImportError: MulticastForwardingStatusCheck`.

- [ ] **Step 3: Implementuj** — přidej do `checks/multicast.py`:

```python
def _upstream_ok(subtype: str | None, upstream: str | None) -> bool:
    if not upstream:
        return False
    prefixes = MVPN_UPSTREAM_PREFIXES if subtype == "mvpn-igmp" else INTERNET_UPSTREAM_PREFIXES
    return upstream.startswith(prefixes)


def _summary(label: str, total: int, failed: int) -> Finding:
    if failed:
        return Finding(
            Outcome.BROKEN, f"{failed} z {total} S,G nefunguje",
            label=label, value=f"{failed}/{total} S,G nefunguje",
        )
    return Finding(Outcome.OK, f"{total} S,G funguje", label=label, value=f"{total} S,G")


@register
class MulticastForwardingStatusCheck(Check):
    id = "multicast_forwarding_status"
    title = "Multicast forwarding na servisnim rozhrani"
    label = "Multicast forwarding status"
    mode = Mode.STATE
    requires = ("igmp_group", "multicast_route")
    requires_inventory = True
    service_types = MULTICAST_TYPES
    service_subtypes = MULTICAST_SUBTYPES
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        pairs = igmp_pairs(ctx.subject, ctx.scope)
        if not pairs:
            # Kaskada (rozhodnuti 2026-09-02): bez IGMP mnoziny neni co hledat.
            return [Finding(
                Outcome.SKIP, "bez IGMP reportu neni co hledat v multicast tabulce",
                value=NO_REPORT_SKIP,
            )]
        table = multicast_table(ctx.subject)
        iface = ctx.scope.selectors.interfaces[0]
        rows: list[Finding] = []
        failed = 0
        for source, group in pairs:
            matches = routes_for(table, source, group)
            if not matches:
                sg = sg_label(source, group)
                rows.append(Finding(
                    Outcome.BROKEN, f"{sg}: S,G neni v multicast tabulce",
                    label="Stream", group=sg, value="S,G neni v multicast tabulce",
                ))
                failed += 1
                continue
            for key, route in matches:
                # Skupina nese realny zdroj z tabulky - u ASM zaznamu (*, G)
                # je to jediny zpusob, jak streamy rozlisit.
                sg = sg_label(key.split(",", 1)[0], group)
                stream = self._stream(sg, iface, ctx.scope.service_subtype, route)
                if any(f.outcome is Outcome.BROKEN for f in stream):
                    failed += 1
                rows.extend(stream)
        return [_summary(self.label, len(pairs), failed), *rows]

    @staticmethod
    def _stream(sg: str, iface: str, subtype: str | None, route: dict[str, Any]) -> list[Finding]:
        downstream = route.get("downstream_interfaces") or []
        on_iface = iface in downstream
        upstream = route.get("upstream_interface")
        return [
            Finding(
                Outcome.OK if on_iface else Outcome.BROKEN,
                f"{sg}: stream se na {iface} " + ("posila" if on_iface else "neposila"),
                label="Stream", group=sg,
                value=(
                    f"Stream se na {iface} posila" if on_iface
                    else f"S,G je v tabulce ale stream se na {iface} neposila"
                ),
            ),
            Finding(
                Outcome.OK if _upstream_ok(subtype, upstream) else Outcome.BROKEN,
                f"{sg}: upstream {upstream or '-'}" + (
                    "" if _upstream_ok(subtype, upstream)
                    else " - S,G je v tabulce ale nema upstream interface"
                ),
                label="Upstream interface", group=sg, value=upstream or "-",
            ),
            *stream_rows(sg, route, rate_label="Forwarding-rate"),
        ]
```

- [ ] **Step 4: Spusť testy**

Run: `pyats-venv/bin/python -m pytest tests/checks/test_multicast.py tests/test_end_to_end.py -q`
Expected: zelená.

- [ ] **Step 5: Ověř řazení v reportu (ruční kontrola)**

Run: `pyats-venv/bin/python -m pytest tests/reporting -q` → zelená. Pak si vyrenderuj syntetický blok (např. dočasný test nebo REPL nad `build_view`) a potvrď, že souhrnný řádek stojí nad skupinami `   -- (S, G)` a skupiny nad `   -- Staticke routy` (AR-37: ungrouped rows first). Nic necommituj z REPL.

- [ ] **Step 6: Commit**

```bash
git add migration_validator/checks/multicast.py tests/checks/test_multicast.py
git commit -m "feat(checks): multicast_forwarding_status - IGMP-rizeny stream/upstream/rate/uptime"
```

---

### Task 9: Check `core_multicast_forwarding` (Core/loopback, inet.2-řízený)

**Files:**
- Modify: `migration_validator/checks/multicast.py`
- Test: `tests/checks/test_multicast.py` (přidat)

**Interfaces:**
- Produces check `core_multicast_forwarding`: `Mode.BOTH`, `requires=("multicast_route", "routes")`, `service_types={"Core"}`, `service_subtypes={"loopback"}`, CRITICAL. Per inet.2 prefix souhrn `Multicast forwarding status` (`Existuje S,G pro {prefix}` / `Neexistuje S,G pro {prefix}` + 4 SKIP), per stream `Upstream interface`, `Downstream interfaces`, `Forwarding rate packets`, `Route uptime`.
- Produces helper `assign_sources(table, prefixes) -> dict[str, list[tuple[str, dict]]]` (longest prefix match, každá routa jednou).
- Consumes: `ctx.scope.selectors.static_routes` (dicty s `rib`, `prefix`, `route_type`), `ctx.subject["routes"]["inet.2"][prefix]["via"]`.

- [ ] **Step 1: Napiš failing testy** — přidej do `tests/checks/test_multicast.py`:

```python
from migration_validator.checks.multicast import CoreMulticastForwardingCheck, assign_sources

PREFIX = "10.11.11.1/32"
INET2 = ({"rib": "inet.2", "prefix": PREFIX, "route_type": "static",
          "next_hops": [{"to": "10.1.1.2", "interface": None, "qualified": False, "active": True}],
          "active": True},)


def _core_scope(static_routes=INET2):
    return _scope("lo0.0", "Core", "loopback", static_routes=static_routes)


def _core_facts(routes=None, via=("et-0/0/0.0",), inet2_present=True):
    facts = {"multicast_route": {"master": routes if routes is not None else {
        f"{SG[0]},{SG[1]}": _route(downstream=["et-0/0/8.11", "irb.2"])}}}
    facts["routes"] = {"inet.2": {PREFIX: {"next_hop": ["10.1.1.2"], "via": list(via),
                                           "active": True, "protocol": "static"}}} if inet2_present else {}
    return facts


def test_assign_sources_longest_prefix_wins_and_each_route_once():
    table = {"10.11.11.1,232.1.1.1": {}, "10.11.12.1,232.1.1.2": {}, "192.0.2.1,232.9.9.9": {}}
    assigned = assign_sources(table, ["10.11.0.0/16", "10.11.11.0/24"])
    assert [k for k, _ in assigned["10.11.11.0/24"]] == ["10.11.11.1,232.1.1.1"]
    assert [k for k, _ in assigned["10.11.0.0/16"]] == ["10.11.12.1,232.1.1.2"]


def test_core_no_inet2_statics_is_silent():
    assert CoreMulticastForwardingCheck().run(_ctx(_core_facts(), scope=_core_scope(()))) == []


def test_core_pass_block_shape():
    findings = CoreMulticastForwardingCheck().run(_ctx(_core_facts(), scope=_core_scope()))
    assert findings[0].outcome is Outcome.OK
    assert findings[0].label == "Multicast forwarding status"
    assert findings[0].value == f"Existuje S,G pro {PREFIX}"
    rows = _by_label(findings, sg_label(*SG))
    assert [f.label for f in findings[1:]] == [
        "Upstream interface", "Downstream interfaces", "Forwarding rate packets", "Route uptime"]
    assert rows["Upstream interface"].outcome is Outcome.OK
    assert rows["Upstream interface"].value == "et-0/0/0.0"
    assert rows["Downstream interfaces"].value == "et-0/0/8.11, irb.2"
    assert rows["Forwarding rate packets"].value == "6 pps"


def test_core_no_stream_for_prefix_fails_with_four_skips():
    findings = CoreMulticastForwardingCheck().run(_ctx(_core_facts(routes={}), scope=_core_scope()))
    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == f"Neexistuje S,G pro {PREFIX}"
    assert [(f.outcome, f.label, f.value) for f in findings[1:]] == [
        (Outcome.SKIP, "S,G", ""),
        (Outcome.SKIP, "Forwarding rate packets", ""),
        (Outcome.SKIP, "Upstream interface", ""),
        (Outcome.SKIP, "Downstream interfaces", ""),
    ]


def test_core_upstream_must_be_one_of_via():
    """ECMP / qualified-next-hop: inet.2 routa ma vic via, upstream staci
    jeden z nich."""
    findings = CoreMulticastForwardingCheck().run(_ctx(
        _core_facts(via=("et-0/0/1.0", "et-0/0/0.0")), scope=_core_scope()))
    assert _by_label(findings, sg_label(*SG))["Upstream interface"].outcome is Outcome.OK
    wrong = CoreMulticastForwardingCheck().run(_ctx(_core_facts(via=("et-0/0/1.0",)), scope=_core_scope()))
    row = _by_label(wrong, sg_label(*SG))["Upstream interface"]
    assert row.outcome is Outcome.BROKEN and row.value == "et-0/0/0.0"


def test_core_inet2_route_missing_from_table_skips_upstream_row():
    findings = CoreMulticastForwardingCheck().run(_ctx(_core_facts(inet2_present=False), scope=_core_scope()))
    row = _by_label(findings, sg_label(*SG))["Upstream interface"]
    assert row.outcome is Outcome.SKIP and row.value == "routa neni v tabulce"


def test_core_no_downstream_fails():
    routes = {f"{SG[0]},{SG[1]}": _route(downstream=[])}
    findings = CoreMulticastForwardingCheck().run(_ctx(_core_facts(routes=routes), scope=_core_scope()))
    row = _by_label(findings, sg_label(*SG))["Downstream interfaces"]
    assert row.outcome is Outcome.BROKEN and row.value == "Zadne downstream interfacy"


def test_core_sg_set_compared_against_baseline():
    """Mutant: smazani vetve DEGRADED pri rozdilu mnozin polozi tento test."""
    baseline = _core_facts(routes={
        f"{SG[0]},{SG[1]}": _route(), "10.11.11.1,232.1.1.9": _route()})
    findings = CoreMulticastForwardingCheck().run(_ctx(
        _core_facts(), baseline=baseline, scope=_core_scope(), baseline_scope=_core_scope()))
    assert findings[0].outcome is Outcome.DEGRADED
    assert findings[0].baseline_value == "(10.11.11.1, 232.1.1.1), (10.11.11.1, 232.1.1.9)"
    same = CoreMulticastForwardingCheck().run(_ctx(
        _core_facts(), baseline=_core_facts(), scope=_core_scope(), baseline_scope=_core_scope()))
    assert same[0].outcome is Outcome.OK
    assert same[0].baseline_value == "(10.11.11.1, 232.1.1.1)"


def test_core_check_applies_only_to_loopback():
    check = CoreMulticastForwardingCheck()
    assert check.applies_to(_core_scope())
    assert not check.applies_to(_scope("et-0/0/0.0", "Core", "transit"))
    assert not check.applies_to(_scope())
```

- [ ] **Step 2: Spusť — musí padnout**

Run: `pyats-venv/bin/python -m pytest tests/checks/test_multicast.py -q -k core`
Expected: `ImportError`.

- [ ] **Step 3: Implementuj** — přidej do `checks/multicast.py`:

```python
CORE_SKIP_LABELS = ("S,G", "Forwarding rate packets", "Upstream interface", "Downstream interfaces")


def assign_sources(
    table: dict[str, dict[str, Any]], prefixes: list[str]
) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    """Routy k inet.2 prefixum podle zdroje - nejdelsi pokryvajici prefix
    vyhrava, kazda routa se pocita jen jednou. Ne-IPv4 prefixy a zdroje
    se preskoci (inet.2 je IPv4 tabulka)."""
    networks = []
    for prefix in prefixes:
        try:
            networks.append((ipaddress.ip_network(prefix, strict=False), prefix))
        except ValueError:
            continue
    networks.sort(key=lambda item: item[0].prefixlen, reverse=True)
    assigned: dict[str, list[tuple[str, dict[str, Any]]]] = {prefix: [] for prefix in prefixes}
    for key, route in sorted(table.items()):
        try:
            source = ipaddress.ip_address(key.split(",", 1)[0])
        except ValueError:
            continue
        for network, prefix in networks:
            if source.version == network.version and source in network:
                assigned[prefix].append((key, route))
                break
    return assigned


def _inet2_prefixes(scope: Scope) -> list[str]:
    return sorted(
        str(route["prefix"])
        for route in scope.selectors.static_routes
        if str(route.get("rib")) == "inet.2"
        and str(route.get("route_type", "static")) == "static"
    )


def _labels_of(streams: list[tuple[str, dict[str, Any]]]) -> str:
    return ", ".join(sg_label(*key.split(",", 1)) for key, _ in streams)


@register
class CoreMulticastForwardingCheck(Check):
    id = "core_multicast_forwarding"
    title = "Multicast forwarding pro inet.2 statiky"
    label = "Multicast forwarding status"
    mode = Mode.BOTH
    requires = ("multicast_route", "routes")
    requires_inventory = True
    service_types = frozenset({"Core"})
    service_subtypes = frozenset({"loopback"})
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        prefixes = _inet2_prefixes(ctx.scope)
        if not prefixes:
            # Bez inet.2 zameru ticho, ne SKIP (rozhodnuti 2026-09-02).
            return []
        assigned = assign_sources(multicast_table(ctx.subject), prefixes)
        baseline_assigned = (
            assign_sources(multicast_table(ctx.baseline), prefixes)
            if ctx.has_baseline else None
        )
        inet2 = (ctx.subject.get("routes") or {}).get("inet.2") or {}
        findings: list[Finding] = []
        for prefix in prefixes:
            streams = assigned.get(prefix, [])
            was = baseline_assigned.get(prefix, []) if baseline_assigned else []
            was_value = _labels_of(was) if was else None
            if not streams:
                findings.append(Finding(
                    Outcome.BROKEN, f"neexistuje S,G se zdrojem v {prefix}",
                    value=f"Neexistuje S,G pro {prefix}", baseline_value=was_value,
                ))
                findings.extend(
                    Finding(Outcome.SKIP, f"{prefix}: bez streamu", label=label, value="")
                    for label in CORE_SKIP_LABELS
                )
                continue
            changed = bool(was) and {k for k, _ in was} != {k for k, _ in streams}
            findings.append(Finding(
                Outcome.DEGRADED if changed else Outcome.OK,
                f"existuje S,G se zdrojem v {prefix}: {_labels_of(streams)}"
                + (" (mnozina se lisi od baseline)" if changed else ""),
                value=f"Existuje S,G pro {prefix}", baseline_value=was_value,
            ))
            measured = inet2.get(prefix)
            vias = list(measured.get("via") or []) if measured else None
            for key, route in streams:
                sg = sg_label(*key.split(",", 1))
                findings.extend(self._stream(sg, route, vias))
        return findings

    @staticmethod
    def _stream(sg: str, route: dict[str, Any], vias: list[str] | None) -> list[Finding]:
        upstream = route.get("upstream_interface")
        if vias is None:
            # FAIL za chybejici inet.2 routu nese static_route_status -
            # tady by byl druhy FAIL za tutez pricinu.
            upstream_row = Finding(
                Outcome.SKIP, f"{sg}: inet.2 routa neni v tabulce, upstream nelze overit",
                label="Upstream interface", group=sg, value="routa neni v tabulce",
            )
        else:
            ok = bool(upstream) and upstream in vias
            upstream_row = Finding(
                Outcome.OK if ok else Outcome.BROKEN,
                f"{sg}: upstream {upstream or '-'}"
                + ("" if ok else f" neni mezi via inet.2 routy ({', '.join(vias) or '-'})"),
                label="Upstream interface", group=sg, value=upstream or "-",
            )
        downstream = route.get("downstream_interfaces") or []
        downstream_row = Finding(
            Outcome.OK if downstream else Outcome.BROKEN,
            f"{sg}: downstream {', '.join(downstream) or 'zadne'}",
            label="Downstream interfaces", group=sg,
            value=", ".join(downstream) if downstream else "Zadne downstream interfacy",
        )
        return [upstream_row, downstream_row, *stream_rows(sg, route, rate_label="Forwarding rate packets")]
```

- [ ] **Step 4: Spusť testy**

Run: `pyats-venv/bin/python -m pytest tests/checks/test_multicast.py tests/test_end_to_end.py -q`
Expected: zelená. Pokud AR-29 padne na `core_multicast_forwarding`, zkontroluj Task 6 syntézu (upstream == via z route zrcadla).

- [ ] **Step 5: Commit**

```bash
git add migration_validator/checks/multicast.py tests/checks/test_multicast.py
git commit -m "feat(checks): core_multicast_forwarding - S,G k inet.2 statikam, upstream proti via"
```

---

### Task 10: Check `mvpn_cmulticast_status` (IPVPN/mvpn-igmp)

**Files:**
- Modify: `migration_validator/checks/multicast.py`
- Test: `tests/checks/test_multicast.py` (přidat)

**Interfaces:**
- Produces check `mvpn_cmulticast_status`: `Mode.BOTH`, `requires=("igmp_group", "mvpn_instance")`, `service_types={"IPVPN"}`, `service_subtypes={"mvpn-igmp"}`, CRITICAL. Řádky per stream: `C-Multicast status`, `Provider tunnel`.
- Consumes: helpery Tasku 7, payload `mvpn_instance` z Tasku 2.

- [ ] **Step 1: Napiš failing testy** — přidej do `tests/checks/test_multicast.py`:

```python
from migration_validator.checks.multicast import MvpnCmulticastStatusCheck

RI = "MULTICAST-STREAM-B-MUX1-RECEIVER"
MSG = ("10.12.12.1", "239.1.1.1")
TUNNEL = "RSVP-TE P2MP:150.0.0.13, 24209,150.0.0.13"


def _mvpn_scope(iface="irb.2"):
    return _scope(iface, "IPVPN", "mvpn-igmp", [RI])


def _entry(tunnel=TUNNEL, pe="150.0.0.13", source=f"{MSG[0]}/32", group=f"{MSG[1]}/32"):
    return {"source_prefix": source, "group_prefix": group, "provider_tunnel_id": tunnel, "sender_pe": pe}


def _mvpn_facts(entries=None, iface="irb.2", pairs=(MSG,), instance_present=True):
    facts = _igmp(iface, *pairs)
    facts["mvpn_instance"] = {RI: {"c_multicast": entries if entries is not None else [_entry()]}} if instance_present else {}
    return facts


def test_mvpn_pass_rows():
    findings = MvpnCmulticastStatusCheck().run(_ctx(_mvpn_facts(), scope=_mvpn_scope()))
    rows = _by_label(findings, sg_label(*MSG))
    assert [f.label for f in findings] == ["C-Multicast status", "Provider tunnel"]
    assert rows["C-Multicast status"].outcome is Outcome.OK
    assert rows["C-Multicast status"].value == "10.12.12.1/32:239.1.1.1/32"
    assert rows["Provider tunnel"].outcome is Outcome.OK
    assert rows["Provider tunnel"].value == TUNNEL


def test_mvpn_without_igmp_is_skip():
    findings = MvpnCmulticastStatusCheck().run(_ctx(_mvpn_facts(pairs=()), scope=_mvpn_scope()))
    assert [(f.outcome, f.label, f.value) for f in findings] == [
        (Outcome.SKIP, "C-Multicast status", NO_REPORT_SKIP)]


def test_mvpn_instance_missing_is_fail():
    findings = MvpnCmulticastStatusCheck().run(_ctx(_mvpn_facts(instance_present=False), scope=_mvpn_scope()))
    assert [(f.outcome, f.value) for f in findings] == [(Outcome.BROKEN, "instance neni v mvpn vypisu")]


def test_mvpn_missing_cmulticast_entry_is_fail():
    findings = MvpnCmulticastStatusCheck().run(_ctx(_mvpn_facts(entries=[]), scope=_mvpn_scope()))
    assert [(f.outcome, f.label, f.value, f.group) for f in findings] == [
        (Outcome.BROKEN, "C-Multicast status", "chybi c-multicast zaznam", sg_label(*MSG))]


def test_mvpn_invalid_tunnel_is_fail():
    findings = MvpnCmulticastStatusCheck().run(_ctx(
        _mvpn_facts(entries=[_entry(tunnel="I-P-tnl:invalid", pe=None)]), scope=_mvpn_scope()))
    row = _by_label(findings, sg_label(*MSG))["Provider tunnel"]
    assert row.outcome is Outcome.BROKEN
    assert row.value == "I-P-tnl:invalid"
    assert "bez provider tunelu" in row.message


def test_mvpn_sender_pe_change_is_warn_but_tunnel_id_change_is_not():
    """Mutant: porovnani celeho retezce misto sender_pe polozi druhou
    polovinu testu (overit spustenim v Tasku 11)."""
    resignaled = "RSVP-TE P2MP:150.0.0.13, 99999,150.0.0.13"
    same_pe = MvpnCmulticastStatusCheck().run(_ctx(
        _mvpn_facts(), baseline=_mvpn_facts(entries=[_entry(tunnel=resignaled)]),
        scope=_mvpn_scope(), baseline_scope=_mvpn_scope()))
    assert _by_label(same_pe, sg_label(*MSG))["Provider tunnel"].outcome is Outcome.OK
    assert _by_label(same_pe, sg_label(*MSG))["Provider tunnel"].baseline_value == resignaled

    other_pe = "RSVP-TE P2MP:150.0.0.11, 24209,150.0.0.11"
    moved = MvpnCmulticastStatusCheck().run(_ctx(
        _mvpn_facts(), baseline=_mvpn_facts(entries=[_entry(tunnel=other_pe, pe="150.0.0.11")]),
        scope=_mvpn_scope(), baseline_scope=_mvpn_scope()))
    assert _by_label(moved, sg_label(*MSG))["Provider tunnel"].outcome is Outcome.DEGRADED


def test_mvpn_asm_report_matches_by_group_only():
    findings = MvpnCmulticastStatusCheck().run(_ctx(
        _mvpn_facts(pairs=((None, MSG[1]),)), scope=_mvpn_scope()))
    assert _by_label(findings, sg_label(None, MSG[1]))["C-Multicast status"].outcome is Outcome.OK


def test_mvpn_check_applies_only_to_mvpn_igmp():
    check = MvpnCmulticastStatusCheck()
    assert check.applies_to(_mvpn_scope())
    assert not check.applies_to(_scope())
    assert not check.applies_to(_scope("irb.3", "IPVPN", None, [RI]))
```

- [ ] **Step 2: Spusť — musí padnout**

Run: `pyats-venv/bin/python -m pytest tests/checks/test_multicast.py -q -k mvpn`
Expected: `ImportError`.

- [ ] **Step 3: Implementuj** — přidej do `checks/multicast.py`:

```python
def _cmulticast_entry(
    entries: list[dict[str, Any]], source: str | None, group: str
) -> dict[str, Any] | None:
    """Zaznam, jehoz S/32:G/32 pokryva IGMP (S,G); pro (*, G) staci group."""
    try:
        group_ip = ipaddress.ip_address(group)
        source_ip = ipaddress.ip_address(source) if source else None
    except ValueError:
        return None
    for entry in entries:
        try:
            if group_ip not in ipaddress.ip_network(entry["group_prefix"], strict=False):
                continue
            if source_ip is not None and source_ip not in ipaddress.ip_network(
                entry["source_prefix"], strict=False
            ):
                continue
        except (KeyError, ValueError):
            continue
        return entry
    return None


@register
class MvpnCmulticastStatusCheck(Check):
    id = "mvpn_cmulticast_status"
    title = "MVPN c-multicast a provider tunnel"
    label = "C-Multicast status"
    mode = Mode.BOTH
    requires = ("igmp_group", "mvpn_instance")
    requires_inventory = True
    service_types = frozenset({"IPVPN"})
    service_subtypes = frozenset({"mvpn-igmp"})
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        pairs = igmp_pairs(ctx.subject, ctx.scope)
        if not pairs:
            return [Finding(
                Outcome.SKIP, "bez IGMP reportu neni co hledat v MVPN", value=NO_REPORT_SKIP,
            )]
        instance = (
            ctx.scope.selectors.routing_instances[0]
            if ctx.scope.selectors.routing_instances else None
        )
        data = (ctx.subject.get("mvpn_instance") or {}).get(instance)
        if data is None:
            return [Finding(
                Outcome.BROKEN, f"instance {instance} neni v mvpn vypisu",
                value="instance neni v mvpn vypisu",
            )]
        entries = data.get("c_multicast") or []
        baseline_entries = (
            ((ctx.baseline or {}).get("mvpn_instance") or {}).get(instance) or {}
        ).get("c_multicast") or []
        findings: list[Finding] = []
        for source, group in pairs:
            sg = sg_label(source, group)
            entry = _cmulticast_entry(entries, source, group)
            if entry is None:
                findings.append(Finding(
                    Outcome.BROKEN, f"{sg}: chybi c-multicast zaznam",
                    label=self.label, group=sg, value="chybi c-multicast zaznam",
                ))
                continue
            findings.append(Finding(
                Outcome.OK, f"{sg}: c-multicast {entry['source_prefix']}:{entry['group_prefix']}",
                label=self.label, group=sg,
                value=f"{entry['source_prefix']}:{entry['group_prefix']}",
            ))
            was = _cmulticast_entry(baseline_entries, source, group) if ctx.has_baseline else None
            findings.append(self._tunnel_row(sg, entry, was))
        return findings

    @staticmethod
    def _tunnel_row(sg: str, entry: dict[str, Any], was: dict[str, Any] | None) -> Finding:
        tunnel = entry.get("provider_tunnel_id") or "-"
        pe = entry.get("sender_pe")
        was_tunnel = was.get("provider_tunnel_id") if was else None
        was_pe = was.get("sender_pe") if was else None
        if not pe:
            outcome, note = Outcome.BROKEN, " - bez provider tunelu"
        elif was_pe and was_pe != pe:
            # Jen sender PE, ne cely retezec: tunnel id se pri re-signalizaci
            # LSP zmeni bez zmeny sluzby (rozhodnuti 2026-09-02).
            outcome, note = Outcome.DEGRADED, f" - sender PE se zmenil z {was_pe}"
        else:
            outcome, note = Outcome.OK, ""
        return Finding(
            outcome, f"{sg}: provider tunnel {tunnel}{note}",
            label="Provider tunnel", group=sg, value=tunnel, baseline_value=was_tunnel,
        )
```

- [ ] **Step 4: Spusť testy**

Run: `pyats-venv/bin/python -m pytest tests/ -q`
Expected: celá suita zelená.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/checks/multicast.py tests/checks/test_multicast.py
git commit -m "feat(checks): mvpn_cmulticast_status - c-multicast zaznam a provider tunnel (sender PE)"
```

---

### Task 11: Mutanty, dokumentace, re-capture `runs/mig01`, roadmap, finální suita

**Files:**
- Modify: `docs/cs/files/checks.md`, `docs/cs/files/collectors.md`, `docs/cs/files/parsers.md`, `docs/cs/files/models.md`, `docs/cs/reference.md` + anglické protějšky v `docs/en/`
- Modify: `runs/mig01/*` (re-capture na schema 8/12)
- Modify: docstringy testů v `tests/checks/test_multicast.py`, které tvrdí mutant kill
- Create: `docs/superpowers/roadmap-2026-09-02-multicast-vlna-hotovo.md`

**Interfaces:**
- Consumes: vše z Tasků 1–10.
- Produces: uzavřená vlna.

- [ ] **Step 1: Mutanty — každý spustit, ne popsat**

Pro každý mutant: proveď změnu, spusť `pyats-venv/bin/python -m pytest tests/ -x -q`, zapiš který test padl, pak `git checkout migration_validator/...`. Výsledek zapiš do docstringu testu, který mutanta zabil (jen pokud opravdu padl — tvrzení bez spuštění je zakázané, viz projektová paměť).

| Mutant | Soubor | Očekávaný zabiják |
|---|---|---|
| `Outcome.DEGRADED` → `Outcome.OK` ve větvi „množina se liší" IGMP checku | `checks/multicast.py` | `test_igmp_report_changed_set_is_warn` |
| `_upstream_ok`: vrať `True` vždy | `checks/multicast.py` | `test_forwarding_internet_upstream_must_be_transit`, `test_forwarding_mvpn_upstream_must_be_lsi_or_vt` |
| `iface in downstream` → `bool(downstream)` | `checks/multicast.py` | `test_forwarding_downstream_without_service_interface_fails_stream_row` |
| `upstream in vias` → `upstream.startswith(("ge-","xe-","et-","ae"))` | `checks/multicast.py` | `test_core_upstream_must_be_one_of_via` |
| `assign_sources`: sort prefixlen vzestupně | `checks/multicast.py` | `test_assign_sources_longest_prefix_wins_and_each_route_once` |
| `_tunnel_row`: porovnej `was_tunnel != tunnel` místo sender PE | `checks/multicast.py` | `test_mvpn_sender_pe_change_is_warn_but_tunnel_id_change_is_not` |
| smaž `if route.rib == "inet.2"` větev | `parsers/core.py` | `test_globalni_inet2_statika_patri_lo0_ne_tranzitu` |
| smaž `measures_multicast` gate (transit dostane tabulku) | `models/scope.py` | `test_core_loopback_gets_master_table_but_transit_does_not` |
| `interface == "local"` filtr pryč | `collectors/multicast.py` | `test_igmp_group_drops_local_pseudo_interface` |

Pokud některý mutant přežije, je to díra v testech: doplň test v příslušném tasku a mutanta přeměř.

- [ ] **Step 2: Dokumentace cs + en**

- `checks.md`: sekce pro čtyři nové checky ve stylu `pim_neighbor_state` (id, `service_types`+subtype, `requires`, pravidla PASS/WARN/FAIL/SKIP, kaskáda bez IGMP, ticho bez inet.2, per-stream skupiny, co se porovnává proti baseline a co záměrně ne, mutant kill věty z kroku 1).
- `collectors.md`: tabulka + odstavec pro `igmp_group` (`show igmp group`), `multicast_route` (`show multicast route instance all extensive`), `mvpn_instance` (`show mvpn instance inet`); payloady, `local` filtr, `0.0.0.0` → None, jen INET, klíč `S,G`, `sender_pe`.
- `parsers.md`: IGMP záměr (globální i RI), subtypy `multicast` / `mvpn-igmp`, inet.2 → lo0.0, globální bridge-domains/vlans a `l2_interface`.
- `models.md`: `ServiceEntry.l2_interface`, `Selectors.l2_interfaces`, schema 8 / 12.
- `reference.md`: řádky tabulky checků pro čtyři nové, schema bumpy, poznámka `L2:` v hlavičce.
- Anglické verze zrcadlí české.

- [ ] **Step 3: Re-capture `runs/mig01`**

Na laborce (heslo viz Global Constraints): přegeneruj `runs/mig01/inventory_*.yml` (skript z Tasku 5 Step 6 s výstupem do těchto cest — filtr na port zachovej podle toho, jak vznikly stávající soubory, viz `docs/cs/README.md` kap. 3a) a snímky přes `mig-validate capture --run mig01 ...` stejným postupem jako předtím. Ověř: snapshoty `schema_version: 12`, inventory 8; `mig-validate evaluate --run mig01` vyrenderuje:
  - Internet blok `MULTICAST-STREAM-A-MUX1-RECEIVER-1` s `IGMP membership report`, souhrnem a skupinou `-- (10.11.11.1, 232.1.1.1)`,
  - lo0.0 blok s `Existuje S,G pro 10.11.11.1/32` a `-- Staticke routy` / `inet.2 10.11.11.1/32`,
  - IPVPN blok `irb.2` s poznámkou `L2: ...` v hlavičce, `C-Multicast status` a `Provider tunnel`.

Vlož výstup jednoho bloku (Internet) do commit message. Pokud se v reálném výstupu něco liší od specu (např. upstream na MX není `ge-` ale `vt-`), je to nález: zapiš ho do roadmapy a **neuprav check, aby prošel** bez rozhodnutí uživatele.

- [ ] **Step 4: Finální suita**

Run: `pyats-venv/bin/python -m pytest tests/ -q`
Expected: vše zelené (počet vzroste z 1272).

- [ ] **Step 5: Roadmap + commit**

Napiš `docs/superpowers/roadmap-2026-09-02-multicast-vlna-hotovo.md` ve stylu předchozích (co vlna přinesla / co vyšlo jinak než spec / co zbývá: igmp snooping check, port checky pro access porty v globální BD, NEZAŘAZENO pro multicast routy bez vlastníka, VRF inet.2).

```bash
git add docs/ runs/mig01 tests/checks/test_multicast.py
git commit -m "docs+data: dokumentace multicast vlny, mutanty, re-capture runs/mig01 (schema 8/12)"
```

---

## Self-review (provedeno při psaní plánu)

- **Pokrytí specu:** sekce 1 (IGMP záměr, subtypy, inet.2 → lo0.0, `l2_interface`, schema 8) → Tasky 3, 4, 5. Sekce 2 (tři collectory, payloady, schema 12, precondition kwargs) → Tasky 1, 2. Sekce 3 (selekce, gate, párování, žádné NEZAŘAZENO) → Tasky 6, 7–10 (`service_types`/`service_subtypes`), test scope id v Tasku 5. Sekce 4 (čtyři checky, řádky, baseline pravidla, layout skupin) → Tasky 7–10. Sekce 5 (fixtures, vrstvy testů, mutanty, docs, re-capture) → Tasky 1, 5, 11. Odloženo → roadmap v Tasku 11.
- **Typová konzistence:** `route_key` a `sg_label` mají všude tvar `"S,G"` / `"(S, G)"`; `multicast_route` je `{instance: {key: route}}` v collectoru, `Scope.select` i `multicast_table`; `Selectors.l2_interfaces` (plurál) vs. `ServiceEntry.l2_interface` (singulár, konvence inventory polí `bridge_domain`, `customer_vlan`) — záměrné, builder překládá.
- **Rozhodnutí přijatá v plánu nad rámec specu (implementátor je nemění):** `family=None` u všech multicast řádků (řádky sedí nad IPv4 sekcí, skupiny streamů před „Staticke routy"); u ASM `(*, G)` reportu se skupina streamu jmenuje podle skutečného zdroje z tabulky; SKIP řádky Core mají `value=""`; IGMP v RI se počítá jako záměr (Task 3), mvpn gate zůstává.
