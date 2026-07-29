# BFD a statické routy — implementační plán

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Naučit nástroj kontrolovat statické routy a BFD — parsovat je z konfigurace jako **záměr**, sbírat přes RPC jako **skutečnost**, a hlásit rozpor mezi nimi i regresi proti baseline.

**Architecture:** Konfigurace se parsuje do inventory (`static_route`, `bfd` na `ServiceEntry`), odkud se stane selektorem scopu. Dva nové collectory (`routes`, `bfd`) přinesou naměřený stav. Dva nové checky iterují přes **sjednocení konfigurace subjektu, měření subjektu a měření baseline** — každý ze tří zdrojů zavírá jednu díru, kterou by jinak nástroj mlčky přešel. Jméno RIB se v parseru normalizuje na tvar, jaký vrací `show route`, takže asymetrie mezi rodinami v konfiguraci se dál nešíří.

**Tech Stack:** Python 3.11+, `jnpr.junos` (PyEZ), `lxml`, `PyYAML`, `pytest`. Spouštění vždy přes `.venv/bin/python` a `.venv/bin/pytest`.

**Spec:** [docs/superpowers/specs/2026-07-29-bfd-a-staticke-routy-design.md](../specs/2026-07-29-bfd-a-staticke-routy-design.md)

## Global Constraints

- **Oba parsery se mění v zámku.** `mx_parser.py` a `evo_parser.py` jsou dva strukturně identické soubory (celkový diff je 146 řádků a týká se výhradně klasifikace VPLS/EVPN, tedy míst, kterých se tato změna nedotýká). Každá změna v jednom musí být provedena i ve druhém, včetně čísel řádků v testech. Testy parserů běží parametrizovaně proti oběma.
- **Jazyk kódu a výstupu:** komentáře, docstringy, jména a všechny uživatelské řetězce v `migration_validator/` jsou **česky bez diakritiky**. Dokumentace v `docs/` je česky **s diakritikou**. Parsery (`mx_parser.py`, `evo_parser.py`) mají české komentáře **s diakritikou** — v nich se drží jejich stávající styl.
- **Collector nikdy neinterpretuje.** Vrací syrová strukturovaná data, žádné verdikty. Kritéria patří do checků.
- **Checky nikdy nesahají na síť.** `migration_validator/checks/` nesmí importovat nic z `migration_validator.connection`.
- **Každý `Finding` musí nastavit `family`.** `reporting/view.py:18` má `FAMILY_ORDER = (None, 4, 6)`; finding bez rodiny spadne do bezhlavičkové sekce nad IPv4 a IPv6 sekcemi. U statické routy se rodina odvozuje **z prefixu**, u BFD **z adresy peeru** — nikdy ze jména RIB (AR-11, stejný důvod jako u `peer_family`).
- **Nový obor faktů se zapisuje na tři místa:** `models/scope.py:FACT_AREAS`, explicitní výčet ve větvi pro service scope v `Scope.select()`, a `capture.py:LIST_AREAS` (jen pokud je obor seznam). `routes` i `bfd` jsou mappingy, takže do `LIST_AREAS` **nepatří** — `_empty()` i `_empty_for()` pro ně vrací `{}` správně už teď.
- **Mutační disciplína.** U tří míst v tomto plánu je povinné: **nejdřív zavést mutanta, spustit test, ověřit že padne, mutanta vrátit, teprve pak psát implementaci.** Vlna 1 ukázala, že strážní test, který se takhle neověří, projde i s rozbitou větví (T13a/T13b). Označená místa: normalizace jména RIB (Task 1), dědění BFD hierarchií (Task 2), podmínka shody routing-instance při mapování routy (Task 1).
- **Testy se spouští z kořene repozitáře:** `.venv/bin/pytest`.
- **Laboratoř:** `172.20.20.4` (vMX, platforma `junos`), `172.20.20.5` (PTX10002-36QDD, platforma `junos-evo`). Uživatel `admin`, autentizace **heslem**, ne klíčem. Heslo je v `~/.bashrc` pod non-interactive guardem, načíst explicitně:
  ```bash
  eval "$(grep '^export MIG_LAB_PASSWORD=' ~/.bashrc)"
  ```
- **Referenční captures z laborky** (mimo git, `runs/` je v `.gitignore`): `runs/bfd-static-2026-07-29/cfg/` je `get-config` obou zařízení, `runs/bfd-static-2026-07-29/rpc/` jsou odpovědi `get-route-information` a `get-bfd-session-information`. Slouží jako zdroj fixtures a jako doklad tvrzení ve specu.

## File Structure

| soubor | odpovědnost | task |
|---|---|---|
| `mx_parser.py`, `evo_parser.py` | `StaticRoute`, `_parse_static_routes`, `_assign_static_routes`, `rib_instance`, filtr v `retrieve_configuration` | 1 |
| `tests/parsers/test_static_routes.py` | *nový* — offline testy statik nad vloženým XML, oba parsery | 1 |
| `mx_parser.py`, `evo_parser.py` | `_parse_bfd`, `_assign_bfd`, `RoutingInstance.bfd`, `self.default_bfd` | 2 |
| `tests/parsers/test_bfd_config.py` | *nový* — offline testy dědění BFD, oba parsery | 2 |
| `mx_parser.py`, `evo_parser.py` | `INVENTORY_SCHEMA_VERSION` 2 → 3, `clean_service_dict` | 3 |
| `migration_validator/models/inventory.py` | `_as_mapping_list`, `ServiceEntry.static_route` / `.bfd`, verze 3 | 3 |
| `migration_validator/models/scope.py` | `Selectors.static_routes` / `.bfd_peers`, `FACT_AREAS`, obě větve `select()` | 4 |
| `migration_validator/scoping/builder.py` | napojení nových selektorů | 4 |
| `migration_validator/models/snapshot.py` | `SCHEMA_VERSION` 2 → 3 | 4 |
| `migration_validator/collectors/routes.py` | *nový* — `show route protocol static` | 5 |
| `migration_validator/collectors/bfd.py` | *nový* — `show bfd session detail` | 6 |
| `migration_validator/collectors/all.py` | registrace obou | 5, 6 |
| `migration_validator/checks/routes.py` | *nový* — `static_route_status` | 7 |
| `migration_validator/checks/bfd.py` | *nový* — `bfd_session_state` | 8 |
| `migration_validator/checks/all.py` | registrace obou | 7, 8 |
| `migration_validator/engine.py` | `_unassigned_static_routes`, `_unassigned_bfd_sessions` | 9 |
| `migration_validator/models/result.py` | `RunResult.unassigned` o dva klíče | 9 |
| `tests/fixtures/rpc/{junos,junos-evo}/{routes,bfd}.xml` | *nové* — nahrané RPC z laborky | 10 |
| `tests/fixtures/172.20.20.{4,5}.yml`, kořenové `172.20.20.{4,5}.yml` | regenerované inventory verze 3 | 10 |
| `docs/cs/**`, `docs/en/**` | popis nových collectorů, checků, polí inventory a klíčů `unassigned` | 10 |

Checky jsou ve **dvou** souborech, ne v jednom `checks/routing.py`: statické routy a BFD nesdílejí ani data, ani pravidla, a `checks/` už dělí po oblastech (`bgp.py`, `evpn.py`, `ifaces.py`, `reachability.py`). Totéž u collectorů.

---

## Task 1: Statické routy v parserech

**Files:**
- Modify: `mx_parser.py` — `InterfaceService` (kolem `:137-159`), `__init__` (`:294-304`), `parse()` (`:306-337`), nová metoda za `_parse_bgp_neighbors`, `_assign_static_routes` za `_assign_bgp_neighbors` (`:1072`), `retrieve_configuration` (`:1753-1764`)
- Modify: `evo_parser.py` — tytéž změny
- Create: `tests/parsers/test_static_routes.py`

**Interfaces:**
- Consumes: nic (první task)
- Produces:
  - `StaticRoute` dataclass s poli `rib: str`, `prefix: str`, `next_hop: list[str]`
  - `rib_instance(rib: str) -> str | None` — modulová funkce
  - `InterfaceService.static_route: list[dict[str, Any]]` — prvky mají klíče `rib`, `prefix`, `next_hop`
  - `JunosServiceParser.static_routes: list[StaticRoute]` (v `evo_parser.py` `JunosEvoAcxServiceParser`)

- [ ] **Step 1: Napsat padající test normalizace jména RIB**

Create `tests/parsers/test_static_routes.py`:

```python
"""Offline testy statickych rout - konfigurace se vklada jako XML, laborka neni potreba.

Oba parsery se meni v zamku, takze kazdy test bezi proti obema.

Tvary XML odpovidaji skutecne konfiguraci laborky z 2026-07-29
(runs/bfd-static-2026-07-29/cfg/): IPv4 lezi primo pod routing-options/static,
IPv6 pod routing-options/rib <jmeno>.inet6.0/static, a to jak globalne, tak
uvnitr routing-instance.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from lxml import etree

ROOT = Path(__file__).resolve().parents[2]


def _load(module_name: str, filename: str):
    """Parsery jsou skripty v korenu repozitare, ne balicek - nacteme je podle cesty."""
    spec = importlib.util.spec_from_file_location(module_name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


evo = _load("evo_parser_static_test", "evo_parser.py")
mx = _load("mx_parser_static_test", "mx_parser.py")

PARSERS = (
    pytest.param(evo, evo.JunosEvoAcxServiceParser, id="evo"),
    pytest.param(mx, mx.JunosServiceParser, id="mx"),
)

BOTH_FAMILIES = """
<configuration>
  <interfaces>
    <interface>
      <name>et-0/0/8</name>
      <unit>
        <name>13</name>
        <description>CPE13-NNI</description>
        <family>
          <inet><address><name>152.11.13.1/29</name></address></inet>
          <inet6><address><name>2001:abcd:11:13::a/64</name></address></inet6>
        </family>
      </unit>
      <unit>
        <name>113</name>
        <description>CPE13-VRF</description>
        <family>
          <inet><address><name>198.11.13.1/29</name></address></inet>
          <inet6><address><name>2001:db8:11:13::a/64</name></address></inet6>
        </family>
      </unit>
    </interface>
  </interfaces>
  <routing-options>
    <static>
      <route>
        <name>198.62.1.0/29</name>
        <next-hop>152.11.13.2</next-hop>
      </route>
    </static>
    <rib>
      <name>inet6.0</name>
      <static>
        <route>
          <name>2001:aaaa::/64</name>
          <next-hop>2001:abcd:11:13::b</next-hop>
        </route>
      </static>
    </rib>
  </routing-options>
  <routing-instances>
    <instance>
      <name>L3VPN-CPE13-NNI</name>
      <instance-type>vrf</instance-type>
      <interface><name>et-0/0/8.113</name></interface>
      <routing-options>
        <static>
          <route>
            <name>172.26.1.0/29</name>
            <next-hop>198.11.13.2</next-hop>
          </route>
        </static>
        <rib>
          <name>L3VPN-CPE13-NNI.inet6.0</name>
          <static>
            <route>
              <name>2001:eeee::/64</name>
              <next-hop>2001:db8:11:13::b</next-hop>
            </route>
          </static>
        </rib>
      </routing-options>
    </instance>
  </routing-instances>
</configuration>
"""


def _parse(module, parser_class, xml: str):
    return parser_class(etree.XML(xml.encode())).parse()


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_rib_names_match_what_show_route_returns(module, parser_class):
    """Obe konfiguracni podoby se normalizuji na jmeno tabulky z RPC.

    Naivni //static/route by nasel obojí, ale ztratil by prislusnost k RIB -
    a prave ta odlisuje ::/0 v mgmt_junos.inet6.0 od ::/0 v inet6.0.
    """
    parser = parser_class(etree.XML(BOTH_FAMILIES.encode()))
    parser.parse()

    found = {(route.rib, route.prefix) for route in parser.static_routes}
    assert found == {
        ("inet.0", "198.62.1.0/29"),
        ("inet6.0", "2001:aaaa::/64"),
        ("L3VPN-CPE13-NNI.inet.0", "172.26.1.0/29"),
        ("L3VPN-CPE13-NNI.inet6.0", "2001:eeee::/64"),
    }


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_rib_instance_maps_global_tables_to_none(module, parser_class):
    """Globalni tabulky patri default instanci, kterou inventory zapisuje jako None."""
    assert module.rib_instance("inet.0") is None
    assert module.rib_instance("inet6.0") is None
    assert module.rib_instance("L3VPN-CPE13-NNI.inet.0") == "L3VPN-CPE13-NNI"
    assert module.rib_instance("L3VPN-CPE13-NNI.inet6.0") == "L3VPN-CPE13-NNI"
    assert module.rib_instance("mgmt_junos.inet6.0") == "mgmt_junos"
```

- [ ] **Step 2: Spustit test a ověřit, že padne**

Run: `.venv/bin/pytest tests/parsers/test_static_routes.py -v`
Expected: FAIL — `AttributeError: 'JunosServiceParser' object has no attribute 'static_routes'` a `AttributeError: module has no attribute 'rib_instance'`.

- [ ] **Step 3: Přidat `StaticRoute` a `rib_instance` do obou parserů**

V `mx_parser.py` **i** `evo_parser.py` za dataclass `InterfaceConfig` (kolem `:117-131`):

```python
@dataclass
class StaticRoute:
    """Jedna statická routa z konfigurace — záměr, ne stav routovací tabulky."""

    rib: str
    prefix: str
    next_hop: list[str] = field(default_factory=list)
```

A za funkci `unique()` (kolem `:232`):

```python
def rib_instance(rib: str) -> str | None:
    """Routing-instance ze jména RIB: 'L3VPN-A.inet6.0' -> 'L3VPN-A', 'inet.0' -> None.

    Globální tabulky patří default instanci, kterou inventory zapisuje jako
    None — stejně jako `routing_instance` služby. Díky tomu jde porovnávat
    přímo, bez zvláštní větve pro globální tabulku.
    """
    head = rib.rsplit(".", 2)[0] if rib.count(".") >= 2 else ""
    return head or None
```

- [ ] **Step 4: Přidat parsování statik do obou parserů**

V `__init__` (`mx_parser.py:294-304`) přidat na konec:

```python
        self.static_routes: list[StaticRoute] = []
```

Za metodu `_parse_bgp_neighbors` (končí `mx_parser.py:518`) vložit:

```python
    def _parse_static_routes(self) -> list[StaticRoute]:
        """Statiky z globálních routing-options i ze všech routing-instances.

        Jméno RIB se normalizuje na tvar, jaký vrací `show route` v poli
        table-name. Konfigurace není mezi rodinami symetrická — IPv4 leží
        přímo pod routing-options/static, IPv6 pod
        routing-options/rib <jméno>.inet6.0/static — ale RPC ten rozdíl nezná.
        Parser ho proto zahladí tady a dál se nešíří.
        """

        routes: list[StaticRoute] = []

        for options_node in self.config_xml.xpath(
            "./*[local-name()='routing-options']"
        ):
            routes.extend(
                self._static_routes_under(options_node, None)
            )

        for instance_node in self.config_xml.xpath(
            "./*[local-name()='routing-instances']"
            "/*[local-name()='instance']"
        ):
            if self._is_inactive(instance_node):
                continue

            instance_name = first_text(
                instance_node,
                "./*[local-name()='name']/text()",
            )

            if not instance_name:
                continue

            for options_node in instance_node.xpath(
                "./*[local-name()='routing-options']"
            ):
                routes.extend(
                    self._static_routes_under(
                        options_node,
                        instance_name,
                    )
                )

        return routes

    def _static_routes_under(
        self,
        options_node: etree._Element,
        instance_name: str | None,
    ) -> list[StaticRoute]:
        """Statiky pod jedním routing-options, s odvozeným jménem RIB."""

        default_rib = (
            f"{instance_name}.inet.0"
            if instance_name
            else "inet.0"
        )

        containers: list[tuple[str, etree._Element]] = [
            (default_rib, static_node)
            for static_node in options_node.xpath(
                "./*[local-name()='static']"
            )
        ]

        for rib_node in options_node.xpath(
            "./*[local-name()='rib']"
        ):
            rib_name = first_text(
                rib_node,
                "./*[local-name()='name']/text()",
            )

            if not rib_name:
                continue

            containers.extend(
                (rib_name, static_node)
                for static_node in rib_node.xpath(
                    "./*[local-name()='static']"
                )
            )

        routes: list[StaticRoute] = []

        for rib_name, static_node in containers:
            for route_node in static_node.xpath(
                "./*[local-name()='route']"
            ):
                if self._is_inactive(route_node):
                    continue

                prefix = first_text(
                    route_node,
                    "./*[local-name()='name']/text()",
                )

                if not prefix:
                    continue

                routes.append(
                    StaticRoute(
                        rib=rib_name,
                        prefix=prefix,
                        # Jen holý next-hop. discard, reject, next-table
                        # a qualified-next-hop nemají adresu k porovnání
                        # se subnetem rozhraní, takže se na službu
                        # nenamapují a skončí v unassigned, pokud jsou
                        # nainstalované. Rozhodnuto ve specu.
                        next_hop=all_texts(
                            route_node,
                            "./*[local-name()='next-hop']/text()",
                        ),
                    )
                )

        return routes
```

V `parse()` (`mx_parser.py:306`) přidat volání hned za `_parse_routing_instances()`:

```python
    def parse(self) -> list[InterfaceService]:
        self._parse_routing_instances()
        self.static_routes = self._parse_static_routes()
        self._parse_default_bgp_neighbors()
```

- [ ] **Step 5: Spustit test a ověřit, že prochází**

Run: `.venv/bin/pytest tests/parsers/test_static_routes.py -v`
Expected: PASS (4 testy — 2 testovací funkce × 2 parsery)

- [ ] **Step 6: MUTAČNÍ OVĚŘENÍ normalizace RIB**

V `_static_routes_under` dočasně nahradit:

```python
        default_rib = (
            f"{instance_name}.inet.0"
            if instance_name
            else "inet.0"
        )
```

za:

```python
        default_rib = "inet.0"
```

Run: `.venv/bin/pytest tests/parsers/test_static_routes.py -v`
Expected: FAIL v `test_rib_names_match_what_show_route_returns` — chybí `("L3VPN-CPE13-NNI.inet.0", "172.26.1.0/29")`, přebývá druhá routa pod `inet.0`.

Pokud test **projde**, je vadný test, ne implementace — oprav test, ne mutanta. Po ověření mutanta vrátit.

- [ ] **Step 7: Commit**

```bash
git add tests/parsers/test_static_routes.py mx_parser.py evo_parser.py
git commit -m "feat(parser): cist staticke routy s normalizovanym jmenem RIB

Obe konfiguracni podoby (IPv4 pod routing-options/static, IPv6 pod
routing-options/rib <jmeno>.inet6.0/static) se prevadeji na jmeno
tabulky, jake vraci show route. Asymetrie mezi rodinami se tim zastavi
na hranici inventare."
```

- [ ] **Step 8: Napsat padající test mapování routy na službu**

Do `tests/parsers/test_static_routes.py` přidat:

```python
UNMAPPABLE = """
<configuration>
  <interfaces>
    <interface>
      <name>et-0/0/8</name>
      <unit>
        <name>113</name>
        <description>CPE13-VRF</description>
        <family>
          <inet><address><name>198.11.13.1/29</name></address></inet>
        </family>
      </unit>
    </interface>
    <interface>
      <name>fxp0</name>
      <unit>
        <name>0</name>
        <family>
          <inet><address><name>10.0.0.15/24</name></address></inet>
        </family>
      </unit>
    </interface>
  </interfaces>
  <routing-instances>
    <instance>
      <name>L3VPN-CPE13-NNI</name>
      <instance-type>vrf</instance-type>
      <interface><name>et-0/0/8.113</name></interface>
      <routing-options>
        <static>
          <route>
            <name>172.26.1.0/29</name>
            <next-hop>198.11.13.2</next-hop>
          </route>
        </static>
      </routing-options>
    </instance>
    <instance>
      <name>mgmt_junos</name>
      <routing-options>
        <static>
          <route>
            <name>0.0.0.0/0</name>
            <next-hop>10.0.0.2</next-hop>
          </route>
        </static>
      </routing-options>
    </instance>
  </routing-instances>
</configuration>
"""

WRONG_VRF = """
<configuration>
  <interfaces>
    <interface>
      <name>et-0/0/8</name>
      <unit>
        <name>113</name>
        <description>CPE13-VRF</description>
        <family>
          <inet><address><name>198.11.13.1/29</name></address></inet>
        </family>
      </unit>
    </interface>
  </interfaces>
  <routing-instances>
    <instance>
      <name>L3VPN-CPE13-NNI</name>
      <instance-type>vrf</instance-type>
      <interface><name>et-0/0/8.113</name></interface>
    </instance>
    <instance>
      <name>L3VPN-JINA</name>
      <instance-type>vrf</instance-type>
      <routing-options>
        <static>
          <route>
            <name>10.9.9.0/24</name>
            <next-hop>198.11.13.2</next-hop>
          </route>
        </static>
      </routing-options>
    </instance>
  </routing-instances>
</configuration>
"""


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_route_lands_on_service_whose_subnet_contains_next_hop(module, parser_class):
    services = _parse(module, parser_class, BOTH_FAMILIES)
    by_interface = {service.interface: service for service in services}

    assert {
        (route["rib"], route["prefix"])
        for route in by_interface["et-0/0/8.113"].static_route
    } == {
        ("L3VPN-CPE13-NNI.inet.0", "172.26.1.0/29"),
        ("L3VPN-CPE13-NNI.inet6.0", "2001:eeee::/64"),
    }


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_next_hop_is_carried_as_value(module, parser_class):
    services = _parse(module, parser_class, BOTH_FAMILIES)
    by_interface = {service.interface: service for service in services}

    routes = {
        route["prefix"]: route["next_hop"]
        for route in by_interface["et-0/0/8.113"].static_route
    }
    assert routes["172.26.1.0/29"] == ["198.11.13.2"]
    assert routes["2001:eeee::/64"] == ["2001:db8:11:13::b"]


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_management_route_lands_on_no_service(module, parser_class):
    """Statika v mgmt_junos padne na fxp0.0, ze ktere se scope nikdy nestane.

    Do inventory se nedostane. Kdyz je nainstalovana, chyti ji
    RunResult.unassigned z routovaci tabulky - viz Task 9.
    """
    services = _parse(module, parser_class, UNMAPPABLE)

    assert all(
        route["prefix"] != "0.0.0.0/0"
        for service in services
        for route in service.static_route
    )


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_next_hop_in_foreign_vrf_does_not_match(module, parser_class):
    """Shoda subnetu sama nestaci - musi sedet i routing-instance.

    Next-hop 198.11.13.2 padne do subnetu et-0/0/8.113, ale routa lezi
    v L3VPN-JINA. Bez podminky na instanci by sedla na spatnou sluzbu.
    """
    services = _parse(module, parser_class, WRONG_VRF)

    assert all(not service.static_route for service in services)
```

- [ ] **Step 9: Spustit test a ověřit, že padne**

Run: `.venv/bin/pytest tests/parsers/test_static_routes.py -v`
Expected: FAIL — `AttributeError: 'InterfaceService' object has no attribute 'static_route'`

- [ ] **Step 10: Přidat pole a mapování do obou parserů**

Do `InterfaceService` (`mx_parser.py:147`, za `bgp_neighbor`):

```python
    bgp_neighbor: list[str] = field(default_factory=list)
    static_route: list[dict[str, Any]] = field(default_factory=list)
```

Za `_bgp_neighbor_matches_interface` (končí `mx_parser.py:1141`) vložit:

```python
    def _assign_static_routes(
        self,
        services: list[InterfaceService],
        interface_configs_by_name: dict[str, InterfaceConfig],
    ) -> None:
        """Routa patří službě, která má next-hop ve svém subnetu a leží v téže RIB.

        Obě podmínky musí platit současně. Bez shody routing-instance by
        next-hop, který náhodou padne do subnetu rozhraní v jiné VRF, sedl
        na špatnou službu.

        Na rozdíl od `_assign_bgp_neighbors` tu není filtr na service_type:
        L2 rozhraní nemá IP adresu, takže se namatchovat nemůže, a filtr by
        byl duplikát podmínky, kterou už dělá shoda adres.
        """

        for service in services:
            interface = interface_configs_by_name.get(service.interface)

            if interface is None:
                continue

            matched = [
                route
                for route in self.static_routes
                if rib_instance(route.rib) == service.routing_instance
                and any(
                    self._bgp_neighbor_matches_interface(
                        next_hop,
                        interface,
                    )
                    for next_hop in route.next_hop
                )
            ]

            if not matched:
                continue

            service.static_route = [
                asdict(route)
                for route in matched
            ]
            service.detection_reason.append(
                "Statická routa odpovídá subnetu rozhraní: "
                + ", ".join(route.prefix for route in matched)
            )
```

V `parse()` přidat volání hned za `_assign_bgp_neighbors(...)`:

```python
        self._assign_static_routes(
            services,
            interface_configs_by_name,
        )
```

- [ ] **Step 11: Spustit test a ověřit, že prochází**

Run: `.venv/bin/pytest tests/parsers/test_static_routes.py -v`
Expected: PASS (12 testů)

- [ ] **Step 12: MUTAČNÍ OVĚŘENÍ podmínky na routing-instance**

V `_assign_static_routes` dočasně smazat řádek:

```python
                if rib_instance(route.rib) == service.routing_instance
```

Run: `.venv/bin/pytest tests/parsers/test_static_routes.py -v`
Expected: FAIL v `test_next_hop_in_foreign_vrf_does_not_match`.

Po ověření mutanta vrátit.

- [ ] **Step 13: Doplnit `routing-options` do filtru konfigurace**

V obou parserech v `retrieve_configuration` (`mx_parser.py:1753`):

```python
    config_filter = etree.XML(
        b"""
        <configuration>
            <interfaces/>
            <routing-options/>
            <routing-instances/>
            <protocols/>
            <bridge-domains/>
            <vlans/>
            <switch-options/>
        </configuration>
        """
    )
```

Bez toho parser globální statiky vůbec neuvidí — VRF varianta se veze uvnitř `<routing-instances/>`, ale globální ne. Testy to nechytí, protože si XML vkládají samy.

Aktualizovat i docstring metody, který vyjmenovává, co se načítá: přidat řádek `routing-options`.

- [ ] **Step 14: Ověřit proti skutečné konfiguraci z laborky**

```bash
.venv/bin/python - <<'EOF'
import importlib.util, sys
from pathlib import Path
from lxml import etree

spec = importlib.util.spec_from_file_location("mxp", "mx_parser.py")
mxp = importlib.util.module_from_spec(spec)
sys.modules["mxp"] = mxp
spec.loader.exec_module(mxp)

root = etree.parse("runs/bfd-static-2026-07-29/cfg/172.20.20.4.raw.xml").getroot()
config = mxp.find_configuration_root(root)
parser = mxp.JunosServiceParser(config)
services = parser.parse()

print("vsechny statiky:")
for route in parser.static_routes:
    print(f"  {route.rib:28} {route.prefix:20} {route.next_hop}")
print("namapovane na sluzby:")
for service in services:
    for route in service.static_route:
        print(f"  {service.interface:16} {route['rib']:28} {route['prefix']}")
EOF
```

Expected: 6 rout celkem (4 servisní + 2 mgmt), z toho **4 namapované** — `172.26.1.0/29` a `2001:eeee::/64` na rozhraní v `L3VPN-CPE13-NNI`, `198.62.1.0/29` a `2001:aaaa::/64` na rozhraní v globální instanci. Obě `mgmt_junos` routy namapované **nejsou**.

- [ ] **Step 15: Commit**

```bash
git add tests/parsers/test_static_routes.py mx_parser.py evo_parser.py
git commit -m "feat(parser): mapovat staticke routy na sluzby

Routa patri sluzbe, ktera ma next-hop ve svem subnetu a lezi v teze
routing-instance. Bez druhe podminky by next-hop, ktery nahodou padne
do subnetu rozhrani v jine VRF, sedl na spatnou sluzbu.

Filtr konfigurace dostal routing-options - bez nej parser globalni
statiky vubec nevidel."
```

---

## Task 2: BFD v parserech s děděním hierarchií

**Files:**
- Modify: `mx_parser.py` — `RoutingInstance` (`:101-115`), `__init__`, `_parse_routing_instances` (`:368-441`), `_parse_default_bgp_neighbors` (`:616-628`), nové metody, `parse()`
- Modify: `evo_parser.py` — tytéž změny
- Create: `tests/parsers/test_bfd_config.py`

**Interfaces:**
- Consumes: `InterfaceService` z Tasku 1
- Produces:
  - `InterfaceService.bfd: list[dict[str, Any]]` — prvky mají klíče `peer`, `minimum_interval`, `multiplier`, `source`
  - `RoutingInstance.bfd: dict[str, dict[str, Any]]` (klíč je adresa peeru)
  - `JunosServiceParser.default_bfd: dict[str, dict[str, Any]]`

- [ ] **Step 1: Napsat padající testy dědění**

Create `tests/parsers/test_bfd_config.py`:

```python
"""Offline testy BFD z konfigurace - dedeni neighbor > group > protocols bgp.

Junosi `inherit` tuhle hierarchii NErozbaluje. Overeno proti laborce
2026-07-29: na 172.20.20.5 je BFD na skupine CPE14 a jeji dva sousede
zadne bfd-liveness-detection nemaji - ani v konfiguraci stazene s inherit.
`inherit` rozbaluje apply-groups, ne hierarchii protokolu. Pruchod si proto
musi udelat parser sam.

Oba parsery se meni v zamku, takze kazdy test bezi proti obema.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from lxml import etree

ROOT = Path(__file__).resolve().parents[2]


def _load(module_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


evo = _load("evo_parser_bfd_test", "evo_parser.py")
mx = _load("mx_parser_bfd_test", "mx_parser.py")

PARSERS = (
    pytest.param(evo, evo.JunosEvoAcxServiceParser, id="evo"),
    pytest.param(mx, mx.JunosServiceParser, id="mx"),
)

INTERFACES = """
  <interfaces>
    <interface>
      <name>et-0/0/8</name>
      <unit>
        <name>13</name>
        <description>CPE13-NNI</description>
        <family>
          <inet><address><name>152.11.13.1/29</name></address></inet>
          <inet6><address><name>2001:abcd:11:13::a/64</name></address></inet6>
        </family>
      </unit>
      <unit>
        <name>114</name>
        <description>CPE14-VRF</description>
        <family>
          <inet><address><name>198.11.14.1/29</name></address></inet>
          <inet6><address><name>2001:db8:11:14::a/64</name></address></inet6>
        </family>
      </unit>
    </interface>
  </interfaces>
"""

# Skupina CPE14 nese BFD, jeji dva sousede vlastni nemaji - presne tvar,
# ktery je od 2026-07-29 v laborce.
GROUP_LEVEL = f"""
<configuration>
{INTERFACES}
  <routing-instances>
    <instance>
      <name>L3VPN-CPE14-UNI</name>
      <instance-type>vrf</instance-type>
      <interface><name>et-0/0/8.114</name></interface>
      <protocols>
        <bgp>
          <group>
            <name>CPE14</name>
            <bfd-liveness-detection>
              <minimum-interval>3000</minimum-interval>
              <multiplier>3</multiplier>
            </bfd-liveness-detection>
            <neighbor><name>198.11.14.2</name></neighbor>
            <neighbor><name>2001:db8:11:14::b</name></neighbor>
          </group>
        </bgp>
      </protocols>
    </instance>
  </routing-instances>
</configuration>
"""

# Soused ma vlastni, JINE timery nez skupina - specifictejsi musi vyhrat.
NEIGHBOR_OVERRIDES_GROUP = f"""
<configuration>
{INTERFACES}
  <protocols>
    <bgp>
      <group>
        <name>CPE</name>
        <bfd-liveness-detection>
          <minimum-interval>3000</minimum-interval>
          <multiplier>3</multiplier>
        </bfd-liveness-detection>
        <neighbor>
          <name>152.11.13.2</name>
          <bfd-liveness-detection>
            <minimum-interval>300</minimum-interval>
            <multiplier>5</multiplier>
          </bfd-liveness-detection>
        </neighbor>
        <neighbor><name>2001:abcd:11:13::b</name></neighbor>
      </group>
    </bgp>
  </protocols>
</configuration>
"""

PROTOCOL_LEVEL = f"""
<configuration>
{INTERFACES}
  <protocols>
    <bgp>
      <bfd-liveness-detection>
        <minimum-interval>1000</minimum-interval>
        <multiplier>3</multiplier>
      </bfd-liveness-detection>
      <group>
        <name>CPE</name>
        <neighbor><name>152.11.13.2</name></neighbor>
      </group>
    </bgp>
  </protocols>
</configuration>
"""

NO_BFD = f"""
<configuration>
{INTERFACES}
  <protocols>
    <bgp>
      <group>
        <name>CPE</name>
        <neighbor><name>152.11.13.2</name></neighbor>
      </group>
    </bgp>
  </protocols>
</configuration>
"""


def _bfd_by_peer(module, parser_class, xml: str) -> dict[str, dict]:
    services = parser_class(etree.XML(xml.encode())).parse()
    return {
        intent["peer"]: intent
        for service in services
        for intent in service.bfd
    }


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_group_level_bfd_reaches_every_neighbor_in_group(module, parser_class):
    """Vcetne IPv6 souseda - skupinove pravidlo neni na rodinu vazane."""
    intents = _bfd_by_peer(module, parser_class, GROUP_LEVEL)

    assert set(intents) == {"198.11.14.2", "2001:db8:11:14::b"}
    for peer in intents:
        assert intents[peer]["minimum_interval"] == 3000
        assert intents[peer]["multiplier"] == 3
        assert intents[peer]["source"] == "group"


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_neighbor_level_overrides_group_level(module, parser_class):
    """Specifictejsi uroven prepisuje obecnejsi, a to celou hodnotou."""
    intents = _bfd_by_peer(module, parser_class, NEIGHBOR_OVERRIDES_GROUP)

    assert intents["152.11.13.2"]["minimum_interval"] == 300
    assert intents["152.11.13.2"]["multiplier"] == 5
    assert intents["152.11.13.2"]["source"] == "neighbor"

    assert intents["2001:abcd:11:13::b"]["minimum_interval"] == 3000
    assert intents["2001:abcd:11:13::b"]["source"] == "group"


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_protocol_level_bfd_reaches_neighbor_through_group(module, parser_class):
    intents = _bfd_by_peer(module, parser_class, PROTOCOL_LEVEL)

    assert intents["152.11.13.2"]["minimum_interval"] == 1000
    assert intents["152.11.13.2"]["source"] == "bgp"


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_no_bfd_means_no_intent(module, parser_class):
    """Sluzba bez BFD nema v inventory prazdny zaznam, ma prazdny seznam."""
    intents = _bfd_by_peer(module, parser_class, NO_BFD)

    assert intents == {}
```

- [ ] **Step 2: Spustit testy a ověřit, že padnou**

Run: `.venv/bin/pytest tests/parsers/test_bfd_config.py -v`
Expected: FAIL — `AttributeError: 'InterfaceService' object has no attribute 'bfd'`

- [ ] **Step 3: Přidat pomocné funkce a pole do obou parserů**

Za funkci `rib_instance` (z Tasku 1) vložit:

```python
def _bfd_node(node: etree._Element | None) -> etree._Element | None:
    """Element bfd-liveness-detection přímo pod daným uzlem, bez sestupu."""
    if node is None:
        return None

    found = node.xpath("./*[local-name()='bfd-liveness-detection']")

    return found[0] if found else None


def _bfd_values(
    node: etree._Element | None,
    source: str,
) -> dict[str, Any] | None:
    """Hodnoty jedné úrovně BFD. None znamená, že na této úrovni nic není."""
    if node is None:
        return None

    return {
        "minimum_interval": _optional_int(
            first_text(
                node,
                "./*[local-name()='minimum-interval']/text()",
            )
        ),
        "multiplier": _optional_int(
            first_text(
                node,
                "./*[local-name()='multiplier']/text()",
            )
        ),
        "source": source,
    }


def _optional_int(value: str | None) -> int | None:
    if value is None or not value.isdigit():
        return None

    return int(value)
```

Do `RoutingInstance` (`mx_parser.py:114`, za `bgp_neighbors`):

```python
    bgp_neighbors: list[str] = field(default_factory=list)
    bfd: dict[str, dict[str, Any]] = field(default_factory=dict)
```

Do `InterfaceService` za `static_route` (z Tasku 1):

```python
    static_route: list[dict[str, Any]] = field(default_factory=list)
    bfd: list[dict[str, Any]] = field(default_factory=list)
```

Do `__init__` za `self.default_bgp_neighbors`:

```python
        self.default_bfd: dict[str, dict[str, Any]] = {}
```

- [ ] **Step 4: Přidat průchod hierarchií**

Za `_parse_bgp_neighbors` (a za metody ze Tasku 1) vložit:

```python
    def _parse_bfd(
        self,
        node: etree._Element,
        bgp_xpath: str,
    ) -> dict[str, dict[str, Any]]:
        """BFD podle peeru, s děděním neighbor > group > protocols bgp.

        Specifičtější úroveň přepisuje obecnější, a to **celou hodnotou**,
        ne položku po položce: soused s vlastním minimum-interval si
        nedědí multiplier ze skupiny.

        Junosí `inherit` tuhle hierarchii nerozbaluje — rozbaluje
        apply-groups, ne hierarchii protokolu. Ověřeno proti laborce
        2026-07-29, kdy skupina CPE14 nesla BFD a její sousedé ho neměli
        ani v konfiguraci stažené s `inherit`.
        """

        intents: dict[str, dict[str, Any]] = {}

        for bgp_node in node.xpath(bgp_xpath):
            protocol_level = _bfd_values(
                _bfd_node(bgp_node),
                "bgp",
            )

            # Soused může viset přímo pod bgp i pod skupinou. Kontejnery
            # se procházejí zvlášť a jen o úroveň níž, aby se soused
            # ve skupině nezapočítal dvakrát.
            containers: list[
                tuple[etree._Element, dict[str, Any] | None]
            ] = [(bgp_node, protocol_level)]

            for group_node in bgp_node.xpath(
                "./*[local-name()='group']"
            ):
                if self._is_inactive(group_node):
                    continue

                containers.append(
                    (
                        group_node,
                        _bfd_values(
                            _bfd_node(group_node),
                            "group",
                        )
                        or protocol_level,
                    )
                )

            for container, inherited in containers:
                for neighbor_node in container.xpath(
                    "./*[local-name()='neighbor']"
                ):
                    if self._is_inactive(neighbor_node):
                        continue

                    peer = first_text(
                        neighbor_node,
                        "./*[local-name()='name']/text()",
                    ) or first_text(neighbor_node, "./text()")

                    if not peer:
                        continue

                    values = _bfd_values(
                        _bfd_node(neighbor_node),
                        "neighbor",
                    ) or inherited

                    if values is not None:
                        intents[peer] = {"peer": peer, **values}

        return intents

    def _assign_bfd(
        self,
        services: list[InterfaceService],
    ) -> None:
        """BFD se připíná jen k peerům, které služba už má v bgp_neighbor.

        Musí běžet **až po** `_assign_bgp_neighbors` — dřív je seznam
        peerů prázdný a nebylo by co spárovat.

        Na rozdíl od `_assign_bgp_neighbors` tu není filtr na service_type.
        Služba bez routing-instance sahá do `self.default_bfd`, což je
        záměr z globálního `protocols bgp` — a to i tehdy, jde-li o Core
        nebo E-LAN. Nevadí to, protože `_assign_bgp_neighbors` plní
        `bgp_neighbor` jen u Internet a IPVPN, takže ostatním službám
        prázdný seznam ukončí iteraci hned na začátku. Je to podmínka,
        na které tahle metoda stojí, ne shoda náhod — kdyby se filtr
        v `_assign_bgp_neighbors` rozšířil, patří sem gate.
        """

        for service in services:
            if not service.bgp_neighbor:
                continue

            if service.routing_instance:
                instance = self.routing_instances.get(
                    service.routing_instance
                )
                intents = instance.bfd if instance else {}
            else:
                intents = self.default_bfd

            service.bfd = [
                intents[peer]
                for peer in service.bgp_neighbor
                if peer in intents
            ]
```

- [ ] **Step 5: Napojit průchod na oba zdroje BGP konfigurace**

V `_parse_routing_instances` (`mx_parser.py:419`) za `bgp_neighbors=...`:

```python
                bgp_neighbors=self._parse_bgp_neighbors(
                    node,
                    "./*[local-name()='protocols']"
                    "/*[local-name()='bgp']",
                ),
                bfd=self._parse_bfd(
                    node,
                    "./*[local-name()='protocols']"
                    "/*[local-name()='bgp']",
                ),
```

V `_parse_default_bgp_neighbors` (`mx_parser.py:616`) na konec metody:

```python
        self.default_bfd = self._parse_bfd(
            self.config_xml,
            "./*[local-name()='protocols']"
            "/*[local-name()='bgp']",
        )
```

V `parse()` přidat volání za `_assign_static_routes(...)`:

```python
        self._assign_bfd(services)
```

- [ ] **Step 6: Spustit testy a ověřit, že prochází**

Run: `.venv/bin/pytest tests/parsers/test_bfd_config.py -v`
Expected: PASS (8 testů)

- [ ] **Step 7: MUTAČNÍ OVĚŘENÍ dědění ze skupiny**

V `_parse_bfd` dočasně nahradit:

```python
                        _bfd_values(
                            _bfd_node(group_node),
                            "group",
                        )
                        or protocol_level,
```

za:

```python
                        protocol_level,
```

Run: `.venv/bin/pytest tests/parsers/test_bfd_config.py -v`
Expected: FAIL ve `test_group_level_bfd_reaches_every_neighbor_in_group` (prázdné `intents`) i ve `test_neighbor_level_overrides_group_level` (IPv6 soused chybí).

Druhý mutant — smazat větev pro `neighbor`:

```python
                    values = inherited
```

Expected: FAIL ve `test_neighbor_level_overrides_group_level` — `152.11.13.2` má `minimum_interval` 3000 místo 300.

Po ověření oba mutanty vrátit.

- [ ] **Step 8: Ověřit proti skutečné konfiguraci z laborky**

```bash
.venv/bin/python - <<'EOF'
import importlib.util, sys
from lxml import etree

spec = importlib.util.spec_from_file_location("evop", "evo_parser.py")
evop = importlib.util.module_from_spec(spec)
sys.modules["evop"] = evop
spec.loader.exec_module(evop)

root = etree.parse("runs/bfd-static-2026-07-29/cfg/172.20.20.5.raw.xml").getroot()
services = evop.JunosEvoAcxServiceParser(evop.find_configuration_root(root)).parse()

for service in services:
    for intent in service.bfd:
        print(f"  {service.interface:16} {intent}")
EOF
```

Expected: **čtyři** záměry — `152.11.13.2` a `198.11.13.2` se `source: neighbor`, a `198.11.14.2` a `2001:db8:11:14::b` se `source: group` (ty přibyly, když se 2026-07-29 přidalo BFD na skupinu `CPE14`). Všechny `minimum_interval: 3000`, `multiplier: 3`.

- [ ] **Step 9: Commit**

```bash
git add tests/parsers/test_bfd_config.py mx_parser.py evo_parser.py
git commit -m "feat(parser): cist BFD zamer s dedenim hierarchie BGP

Neighbor > group > protocols bgp, specifictejsi uroven prepisuje
obecnejsi celou hodnotou. Junosi inherit tuhle hierarchii nerozbaluje
(overeno v laborce na skupine CPE14), takze pruchod dela parser.

Nese se i hodnota timeru a source - bez nej nejde odlisit 'soused ma
vlastni timery' od 'zdedil je ze skupiny'."
```

---

## Task 3: Inventory model a `schema_version` 3

**Files:**
- Modify: `migration_validator/models/inventory.py`
- Modify: `mx_parser.py:1782` (`INVENTORY_SCHEMA_VERSION`), `:1798-1830` (`clean_service_dict`)
- Modify: `evo_parser.py` — tytéž změny
- Test: `tests/models/test_inventory.py`

**Interfaces:**
- Consumes: `InterfaceService.static_route` a `.bfd` z Tasků 1 a 2
- Produces:
  - `ServiceEntry.static_route: list[dict[str, Any]]`, `ServiceEntry.bfd: list[dict[str, Any]]`
  - `INVENTORY_SCHEMA_VERSION = 3`
  - `_as_mapping_list(value: Any) -> list[dict[str, Any]]`

- [ ] **Step 1: Napsat padající testy**

Do `tests/models/test_inventory.py` přidat:

```python
def test_version_two_inventory_is_rejected(tmp_path):
    """Stara inventory nema static_route ani bfd.

    Tolerantni cteni by tise vratilo sluzby bez statik, takze by check
    'nakonfigurovana routa neni v tabulce' nemel co hlasit a sluzba by
    svitila zelene. Stejny duvod jako u rozdeleni rodin ve verzi 2.
    """
    path = tmp_path / "stara.yml"
    path.write_text(
        "schema_version: 2\ndevice: r1\ninterfaces: []\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="schema_version 2"):
        load_inventory(path)


def test_static_routes_and_bfd_survive_load(tmp_path):
    path = tmp_path / "nova.yml"
    path.write_text(
        """
schema_version: 3
device: r1
interfaces:
  - interface: et-0/0/8.113
    service_type: IPVPN
    static_route:
      - rib: L3VPN-A.inet.0
        prefix: 172.26.1.0/29
        next_hop: [198.11.13.2]
    bfd:
      - peer: 198.11.13.2
        minimum_interval: 3000
        multiplier: 3
        source: group
""",
        encoding="utf-8",
    )

    entry = load_inventory(path).entries[0]

    assert entry.static_route == [
        {
            "rib": "L3VPN-A.inet.0",
            "prefix": "172.26.1.0/29",
            "next_hop": ["198.11.13.2"],
        }
    ]
    assert entry.bfd[0]["source"] == "group"
    assert entry.bfd[0]["minimum_interval"] == 3000


def test_missing_new_fields_default_to_empty(tmp_path):
    path = tmp_path / "bez.yml"
    path.write_text(
        "schema_version: 3\ndevice: r1\n"
        "interfaces:\n  - interface: et-0/0/8.13\n    service_type: Internet\n",
        encoding="utf-8",
    )

    entry = load_inventory(path).entries[0]

    assert entry.static_route == []
    assert entry.bfd == []


def test_mapping_list_rejects_scalars(tmp_path):
    """Prvek, ktery neni mapping, je chyba - ne tichy prevod na retezec.

    _as_list by z {'rib': ...} udelal jeho str() a check by pak hledal
    klice v retezci.
    """
    path = tmp_path / "spatna.yml"
    path.write_text(
        "schema_version: 3\ndevice: r1\n"
        "interfaces:\n  - interface: et-0/0/8.13\n    service_type: Internet\n"
        "    static_route: [not-a-mapping]\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="mapping"):
        load_inventory(path)
```

- [ ] **Step 2: Spustit testy a ověřit, že padnou**

Run: `.venv/bin/pytest tests/models/test_inventory.py -v`
Expected: FAIL — `test_version_two_inventory_is_rejected` projde omylem není možné (verze 2 je pořád platná), ostatní padnou na `AttributeError: 'ServiceEntry' object has no attribute 'static_route'`.

- [ ] **Step 3: Implementovat v `models/inventory.py`**

Za `_as_optional_str` (`:20-23`) vložit:

```python
def _as_mapping_list(value: Any) -> list[dict[str, Any]]:
    """Pole, jehoz prvky jsou mappingy - staticke routy a BFD zamer.

    Na rozdil od _as_list se prvky neprevadeji na retezec: ztratila by se
    struktura, ze ktere check bere identitu routy (rib, prefix) i hodnotu
    (next_hop). Nevalidni tvar je chyba, ne tichy prevod.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"ocekavan seznam mappingu, nalezeno {type(value).__name__}")

    items: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(
                f"ocekavan mapping v seznamu, nalezeno {type(item).__name__}"
            )
        items.append(dict(item))
    return items
```

Do `ServiceEntry` za `customer_vlan` (`:43`):

```python
    customer_vlan: list[str] = field(default_factory=list)
    static_route: list[dict[str, Any]] = field(default_factory=list)
    bfd: list[dict[str, Any]] = field(default_factory=list)
```

Do `from_dict` za `customer_vlan=...` (`:72`):

```python
            customer_vlan=_as_list(data.get("customer_vlan")),
            static_route=_as_mapping_list(data.get("static_route")),
            bfd=_as_mapping_list(data.get("bfd")),
```

Do `to_dict` za `"customer_vlan"` (`:90`):

```python
            "customer_vlan": list(self.customer_vlan),
            "static_route": [dict(route) for route in self.static_route],
            "bfd": [dict(intent) for intent in self.bfd],
```

Změnit `:100`:

```python
INVENTORY_SCHEMA_VERSION = 3
```

Rozšířit docstring `load_inventory` (`:104-109`) o druhý odstavec:

```python
    """Nacte YAML vystup parseru konfigurace.

    Stara inventory se odmita, ne dopocitava. Pole adres se prejmenovala na
    rodiny; tolerantni cteni by u starsiho souboru tise vratilo sluzby bez
    adres, takze by neprobehl ping a sluzba by presto svitila zelene.

    Verze 3 pridala static_route a bfd. Tolerantni cteni ma tady stejnou
    cenu: sluzba by prisla bez zameru, takze by check nemel co porovnat
    s routovaci tabulkou a rozpor mezi konfiguraci a stavem by zmizel.
    """
```

- [ ] **Step 4: Spustit testy a ověřit, že prochází**

Run: `.venv/bin/pytest tests/models/test_inventory.py -v`
Expected: PASS

- [ ] **Step 5: Zvýšit verzi a doplnit klíče v obou parserech**

V `mx_parser.py:1782` **i** `evo_parser.py`:

```python
INVENTORY_SCHEMA_VERSION = 3
```

V `clean_service_dict` do `ordered_keys` za `"customer_vlan"`:

```python
        "customer_vlan",
        "static_route",
        "bfd",
        "detection_confidence",
        "detection_reason",
```

- [ ] **Step 6: Ověřit shodu verzí napříč repem**

Run:
```bash
grep -rn "INVENTORY_SCHEMA_VERSION = " mx_parser.py evo_parser.py migration_validator/models/inventory.py
```
Expected: tři řádky, všechny `= 3`.

- [ ] **Step 7: Spustit celou sadu**

Run: `.venv/bin/pytest`
Expected: testy, které načítají `tests/fixtures/172.20.20.*.yml`, teď **padnou** na `schema_version 2`. To je očekávané — fixtures se regenerují v Tasku 10. Zapiš si, které to jsou, ať víš, co má na konci zezelenat:

```bash
.venv/bin/pytest 2>&1 | grep -E "^(FAILED|ERROR)" | sort -u
```

- [ ] **Step 8: Commit**

```bash
git add migration_validator/models/inventory.py mx_parser.py evo_parser.py tests/models/test_inventory.py
git commit -m "feat(inventory): static_route a bfd v zaznamu sluzby, schema_version 3

Prvky jsou mappingy, ne retezce - _as_list by z nich udelal str() a check
by pak hledal klice v retezci. Nevalidni tvar je chyba, ne tichy prevod.

Fixtures inventory jsou do jejich regenerace nekompatibilni; testy, ktere
je nacitaji, do Tasku 10 padaji."
```

---

## Task 4: Selektory, scope, builder a verze snapshotu

**Files:**
- Modify: `migration_validator/models/scope.py:15` (`FACT_AREAS`), `:41-72` (`Selectors`), `:102-148` (`select()`)
- Modify: `migration_validator/scoping/builder.py:69-82`
- Modify: `migration_validator/models/snapshot.py:17`
- Test: `tests/models/test_scope.py`

**Interfaces:**
- Consumes: `ServiceEntry.static_route` a `.bfd` z Tasku 3
- Produces:
  - `Selectors.static_routes: list[dict[str, Any]]`, `Selectors.bfd_peers: list[dict[str, Any]]`
  - `Scope.select()` vrací klíče `routes` (`{table: {prefix: {...}}}`) a `bfd` (`{peer: {...}}`)
  - `SCHEMA_VERSION = 3` ve `models/snapshot.py`

- [ ] **Step 1: Napsat padající testy**

Do `tests/models/test_scope.py` přidat:

```python
ROUTE_FACTS = {
    "inet.0": {
        "198.62.1.0/29": {"next_hop": ["152.11.13.2"], "via": ["et-0/0/8.13"], "active": True},
        "10.9.9.0/24": {"next_hop": ["10.9.9.1"], "via": ["et-0/0/9.0"], "active": True},
    },
    "L3VPN-A.inet.0": {
        "172.26.1.0/29": {"next_hop": ["198.11.13.2"], "via": ["et-0/0/8.113"], "active": True},
    },
}

BFD_FACTS = {
    "152.11.13.2": {"state": "Up", "interface": "et-0/0/8.13"},
    "198.11.14.2": {"state": "Down", "interface": "et-0/0/8.114"},
}


def _scope_with(**selector_kwargs) -> Scope:
    return Scope(
        id="svc:test:Internet",
        kind="service",
        key=ScopeKey(description="test", service_type="Internet"),
        selectors=Selectors(**selector_kwargs),
    )


def test_routes_are_selected_by_rib_and_prefix():
    """Identita je (RIB, prefix) - stejny prefix v jine RIB je jina routa."""
    scope = _scope_with(
        static_routes=[
            {"rib": "inet.0", "prefix": "198.62.1.0/29", "next_hop": ["152.11.13.2"]}
        ]
    )

    selected = scope.select({"routes": ROUTE_FACTS})

    assert selected["routes"] == {
        "inet.0": {
            "198.62.1.0/29": {
                "next_hop": ["152.11.13.2"],
                "via": ["et-0/0/8.13"],
                "active": True,
            }
        }
    }


def test_table_without_matching_prefix_is_dropped_entirely():
    """Prazdna tabulka by v reportu nic nerekla a check by ji musel preskakovat."""
    scope = _scope_with(
        static_routes=[
            {"rib": "L3VPN-A.inet.0", "prefix": "172.26.1.0/29", "next_hop": []}
        ]
    )

    selected = scope.select({"routes": ROUTE_FACTS})

    assert set(selected["routes"]) == {"L3VPN-A.inet.0"}


def test_bfd_sessions_are_selected_by_bgp_neighbors():
    """Session patri scopu podle peeru, ne podle zameru - viz AR-14.

    Kdyby se vybiralo podle bfd_peers, session peeru, ktereho parser do
    zameru nedoplnil, by se do scope nedostala a chyba v pruchodu hierarchii
    by se schovala pred vystupem nastroje.
    """
    scope = _scope_with(bgp_neighbors=["152.11.13.2"], bfd_peers=[])

    selected = scope.select({"bfd": BFD_FACTS})

    assert set(selected["bfd"]) == {"152.11.13.2"}


def test_device_scope_sees_all_routes_and_sessions():
    """Rezim bez inventory je podle AR-10 doporuceny zpusob prohlidky zarizeni."""
    selected = device_scope().select({"routes": ROUTE_FACTS, "bfd": BFD_FACTS})

    assert selected["routes"] == ROUTE_FACTS
    assert selected["bfd"] == BFD_FACTS


def test_missing_areas_come_back_as_empty_mappings():
    scope = _scope_with()

    selected = scope.select({})

    assert selected["routes"] == {}
    assert selected["bfd"] == {}
```

Do `tests/models/test_snapshot.py` přidat:

```python
def test_version_two_snapshot_is_rejected():
    """Stary snimek nema oblasti routes a bfd.

    Kontrolou verze by prosel, ale failed_collectors by je nevypsalo -
    collector neselhal, on vubec nebezel. bfd_session_state by pak videl
    zamer z inventare, nula session, BGP Established a dal FAIL na kazde
    sluzbe. Falesny poplach, ne ticha zelen.
    """
    with pytest.raises(SnapshotVersionError, match="schema_version 2"):
        Snapshot.from_dict({"schema_version": 2, "device": {}, "capture": {}})
```

- [ ] **Step 2: Spustit testy a ověřit, že padnou**

Run: `.venv/bin/pytest tests/models/test_scope.py tests/models/test_snapshot.py -v`
Expected: FAIL — `TypeError: Selectors.__init__() got an unexpected keyword argument 'static_routes'` a chybějící klíč `routes`.

- [ ] **Step 3: Rozšířit `Selectors` a `FACT_AREAS`**

`models/scope.py:15`:

```python
FACT_AREAS = (
    "interfaces",
    "arp",
    "nd",
    "bgp",
    "evpn_vpws",
    "evpn_esi",
    "evpn_mac",
    "routes",
    "bfd",
)
```

Do `Selectors` za `bridge_domains` (`:51`):

```python
    bridge_domains: list[str] = field(default_factory=list)
    # Zamer z konfigurace. Slouzi zaroven jako filtr (vyber podle
    # (rib, prefix)) i jako mnozina, proti ktere check pozna, ze
    # nakonfigurovana routa v tabulce chybi.
    static_routes: list[dict[str, Any]] = field(default_factory=list)
    # Jen zamer. Session se vybiraji pres bgp_neighbors - jsou to dve
    # ruzne veci a slevat je do jednoho seznamu by znamenalo drzet je
    # v synchronu.
    bfd_peers: list[dict[str, Any]] = field(default_factory=list)
```

Do `to_dict` za `"bridge_domains"` (`:66`):

```python
            "bridge_domains": list(self.bridge_domains),
            "static_routes": [dict(route) for route in self.static_routes],
            "bfd_peers": [dict(intent) for intent in self.bfd_peers],
```

`from_dict` (`:70-72`) zůstává beze změny — `list(data.get(name, []))` funguje i pro seznam mappingů.

- [ ] **Step 4: Rozšířit `Scope.select()`**

Ve větvi pro service scope za `evpn_mac` (`:132-136`) vložit:

```python
        wanted_routes = {
            (str(route.get("rib")), str(route.get("prefix")))
            for route in self.selectors.static_routes
        }
        routes = {}
        for table, prefixes in (facts.get("routes") or {}).items():
            selected_prefixes = {
                prefix: data
                for prefix, data in prefixes.items()
                if (table, prefix) in wanted_routes
            }
            # Prazdna tabulka se nevraci - v reportu by nic nerekla a
            # check by ji musel preskakovat.
            if selected_prefixes:
                routes[table] = selected_prefixes

        # Session patri scopu podle peeru, ne podle zameru: kdyby se
        # vybiralo podle bfd_peers, session peeru, ktereho parser do
        # zameru nedoplnil, by se sem nedostala a chyba v pruchodu
        # hierarchii by se schovala pred vystupem nastroje (AR-14).
        bfd = {
            peer: data
            for peer, data in (facts.get("bfd") or {}).items()
            if peer in self.selectors.bgp_neighbors
        }
```

A do návratového slovníku (`:139-148`):

```python
        return {
            "interfaces": interfaces,
            "arp": arp,
            "nd": nd,
            "bgp": bgp,
            "evpn_vpws": evpn_vpws,
            "evpn_esi": evpn_esi,
            "evpn_mac": evpn_mac,
            "routes": routes,
            "bfd": bfd,
            "ping": ping,
        }
```

`_empty(area)` (`:169-170`) se **nemění** — vrací `{}` pro vše mimo `arp` a `nd`, což je pro obě nové oblasti správně. Totéž `capture.py:LIST_AREAS`.

- [ ] **Step 5: Napojit selektory v builderu**

`scoping/builder.py:80-81`, za `bridge_domains`:

```python
                    bridge_domains=list(entry.bridge_domain),
                    static_routes=[dict(route) for route in entry.static_route],
                    bfd_peers=[dict(intent) for intent in entry.bfd],
```

- [ ] **Step 6: Zvýšit verzi snapshotu**

`models/snapshot.py:17`:

```python
SCHEMA_VERSION = 3
```

- [ ] **Step 7: Spustit testy a ověřit, že prochází**

Run: `.venv/bin/pytest tests/models/ -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add migration_validator/models/scope.py migration_validator/models/snapshot.py migration_validator/scoping/builder.py tests/models/
git commit -m "feat(scope): selektory pro staticke routy a BFD, snapshot verze 3

Routy se vybiraji podle (rib, prefix), BFD session podle bgp_neighbors -
ne podle zameru, aby chybejici zamer session neschoval.

FACT_AREAS i explicitni vetev v select() dostaly obe oblasti; vynechani
FACT_AREAS by rozbilo prave rezim bez inventory."
```

---

## Task 5: Collector `routes`

**Files:**
- Create: `migration_validator/collectors/routes.py`
- Modify: `migration_validator/collectors/all.py`
- Create: `tests/collectors/test_routes.py`
- Create: `tests/fixtures/rpc/junos/routes.xml`, `tests/fixtures/rpc/junos-evo/routes.xml`

**Interfaces:**
- Consumes: `Collector` z `collectors/base.py`, `_text` z `collectors/interfaces.py`
- Produces: `RoutesCollector` s `name = "routes"`, `parse()` vrací `dict[str, dict[str, dict[str, Any]]]` (tabulka → prefix → `{next_hop, via, active}`)

- [ ] **Step 1: Nakopírovat fixtures z laborky**

```bash
cp runs/bfd-static-2026-07-29/rpc/172.20.20.4.route_static.xml tests/fixtures/rpc/junos/routes.xml
cp runs/bfd-static-2026-07-29/rpc/172.20.20.5.route_static.xml tests/fixtures/rpc/junos-evo/routes.xml
```

Ověř obsah:
```bash
grep -c "<rt>" tests/fixtures/rpc/junos/routes.xml tests/fixtures/rpc/junos-evo/routes.xml
```
Expected: `junos/routes.xml` má 2 (obě mgmt), `junos-evo/routes.xml` má 5 (servisní).

- [ ] **Step 2: Napsat padající testy**

Create `tests/collectors/test_routes.py`:

```python
"""Testy collectoru statickych rout proti nahranemu XML z laborky.

Fixtures jsou skutecne odpovedi z 2026-07-29. Na junos (vMX) jsou v tabulce
jen mgmt routy - servisni statiky tam sice nakonfigurovane jsou, ale
nenainstalovaly se, protoze jejich next-hop neexistuje (rozhrani je po
migraci deaktivovane). Prave tenhle rozpor ma check chytat.
"""

from __future__ import annotations

import pytest

from migration_validator.collectors.routes import RoutesCollector

PLATFORMS = ("junos", "junos-evo")


@pytest.mark.parametrize("platform", PLATFORMS)
def test_returns_mapping_of_tables(rpc_fixture, platform):
    result = RoutesCollector().parse(rpc_fixture(platform, "routes"), platform)
    assert isinstance(result, dict)
    assert result, "fixture nema zadnou statickou routu"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_entries_have_expected_keys(rpc_fixture, platform):
    result = RoutesCollector().parse(rpc_fixture(platform, "routes"), platform)
    for prefixes in result.values():
        for data in prefixes.values():
            assert set(data) == {"next_hop", "via", "active"}


def test_table_name_carries_rib_and_family(rpc_fixture):
    """table-name nese RIB i rodinu v jednom poli - proto se na nej normalizuje parser."""
    result = RoutesCollector().parse(rpc_fixture("junos-evo", "routes"), "junos-evo")

    assert result["inet.0"]["198.62.1.0/29"]["next_hop"] == ["152.11.13.2"]
    assert result["inet6.0"]["2001:aaaa::/64"]["next_hop"] == ["2001:abcd:11:13::b"]
    assert result["L3VPN-CPE13-NNI.inet.0"]["172.26.1.0/29"]["via"] == ["et-0/0/8.113"]
    assert "L3VPN-CPE13-NNI.inet6.0" in result


def test_management_routes_are_collected_not_filtered(rpc_fixture):
    """Collector neinterpretuje. Vyrazeni mgmt rout patri do scope, ne sem."""
    result = RoutesCollector().parse(rpc_fixture("junos", "routes"), "junos")

    assert result["mgmt_junos.inet.0"]["0.0.0.0/0"]["via"] == ["fxp0.0"]
    assert result["mgmt_junos.inet6.0"]["::/0"]["next_hop"] == ["2001:db8::1"]


@pytest.mark.parametrize("platform", PLATFORMS)
def test_empty_tables_are_dropped(rpc_fixture, platform):
    """RPC vraci pres dvacet tabulek, vetsina prazdna - ty by snimek jen nafoukly."""
    result = RoutesCollector().parse(rpc_fixture(platform, "routes"), platform)

    assert all(prefixes for prefixes in result.values())


@pytest.mark.parametrize("platform", PLATFORMS)
def test_values_are_stripped(rpc_fixture, platform):
    """MX obaluje hodnoty novymi radky, EVO ne."""
    result = RoutesCollector().parse(rpc_fixture(platform, "routes"), platform)

    for prefixes in result.values():
        for prefix, data in prefixes.items():
            assert prefix == prefix.strip()
            for value in data["next_hop"] + data["via"]:
                assert value == value.strip(), f"nese bile znaky: {value!r}"
```

- [ ] **Step 3: Spustit testy a ověřit, že padnou**

Run: `.venv/bin/pytest tests/collectors/test_routes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'migration_validator.collectors.routes'`

- [ ] **Step 4: Implementovat collector**

Create `migration_validator/collectors/routes.py`:

```python
"""Sber statickych rout z routovaci tabulky.

Collector nerozhoduje, jestli routa chybi nebo prebyva - jen zapise, co
v tabulce je. Porovnani se zamerem z konfigurace patri do checku.

Overeno proti laborce: `table-name` nese jmeno RIB vcetne rodiny
(`L3VPN-CPE13-NNI.inet6.0`), takze asymetrie, kterou ma konfigurace mezi
IPv4 a IPv6, se v RPC nevyskytuje. `via` nese vystupni rozhrani, takze
mapovani na sluzbu nepotrebuje aritmetiku nad next-hopem.
"""

from __future__ import annotations

from typing import Any

from lxml import etree

from migration_validator.collectors.base import Collector
from migration_validator.collectors.interfaces import _text
from migration_validator.collectors.registry import register

STATIC = "static"
ACTIVE_TAG = "*"


def _texts(node: etree._Element, tag: str) -> list[str]:
    """Vsechny neprazdne texty daneho tagu pod uzlem.

    `to` i `via` sedi uvnitr <nh>, ne primo pod <rt-entry>, proto iter().
    """
    values = []
    for element in node.iter(tag):
        value = (element.text or "").strip()
        if value:
            values.append(value)
    return values


@register
class RoutesCollector(Collector):
    name = "routes"

    def rpc_name(self, platform: str) -> str:
        return "get_route_information"

    def rpc_kwargs(self, platform: str) -> dict[str, Any]:
        # Bez `all=True`: ta varianta pridava jen __juniper_private*
        # tabulky, coz je sum. Filtr na protokol drzi odpoved malou i na
        # zarizeni s plnou internetovou tabulkou.
        return {"protocol": STATIC}

    def parse(
        self, xml: etree._Element, platform: str
    ) -> dict[str, dict[str, dict[str, Any]]]:
        tables: dict[str, dict[str, dict[str, Any]]] = {}

        for table in xml.iter("route-table"):
            name = _text(table, "table-name")
            if not name:
                continue

            prefixes: dict[str, dict[str, Any]] = {}
            for route in table.iter("rt"):
                prefix = _text(route, "rt-destination")
                if not prefix:
                    continue

                for entry in route.iter("rt-entry"):
                    # Filtr na protokol uz je v RPC, tohle je pojistka:
                    # nasazeni s jinym filtrem by jinak zapsalo BGP routy
                    # jako staticke.
                    if (_text(entry, "protocol-name") or "").lower() != STATIC:
                        continue

                    prefixes[prefix] = {
                        "next_hop": _texts(entry, "to"),
                        "via": _texts(entry, "via"),
                        "active": _text(entry, "active-tag") == ACTIVE_TAG,
                    }

            # RPC vraci pres dvacet tabulek, vetsina prazdna. Ukladat je
            # znamena nafouknout kazdy snimek o rady, ktere nic nerikaji.
            if prefixes:
                tables[name] = prefixes

        return tables
```

Do `migration_validator/collectors/all.py`:

```python
from migration_validator.collectors import (  # noqa: F401
    arp,
    bgp,
    evpn,
    interfaces,
    nd,
    routes,
)
```

- [ ] **Step 5: Spustit testy a ověřit, že prochází**

Run: `.venv/bin/pytest tests/collectors/test_routes.py -v`
Expected: PASS (14 testů)

- [ ] **Step 6: Commit**

```bash
git add migration_validator/collectors/routes.py migration_validator/collectors/all.py tests/collectors/test_routes.py tests/fixtures/rpc/junos/routes.xml tests/fixtures/rpc/junos-evo/routes.xml
git commit -m "feat(collector): sbirat staticke routy pres show route protocol static

Fakta jsou klicovana table -> prefix, coz odpovida identite routy
(RIB, prefix). Prazdne tabulky se zahazuji - RPC jich vraci pres dvacet
a vetsina nic nerika."
```

---

## Task 6: Collector `bfd`

**Files:**
- Create: `migration_validator/collectors/bfd.py`
- Modify: `migration_validator/collectors/all.py`
- Create: `tests/collectors/test_bfd.py`
- Create: `tests/fixtures/rpc/junos/bfd.xml`, `tests/fixtures/rpc/junos-evo/bfd.xml`

**Interfaces:**
- Consumes: `Collector`, `_text`, `_int`
- Produces: `BfdCollector` s `name = "bfd"`, `parse()` vrací `dict[str, dict[str, Any]]` (peer → `{state, interface, remote_state, local_diagnostic, clients, detection_time, transmission_interval, multiplier}`)

- [ ] **Step 1: Nakopírovat fixtures z laborky**

```bash
cp runs/bfd-static-2026-07-29/rpc/172.20.20.4.bfd_detail.xml tests/fixtures/rpc/junos/bfd.xml
cp runs/bfd-static-2026-07-29/rpc/172.20.20.5.bfd_detail.xml tests/fixtures/rpc/junos-evo/bfd.xml
```

`junos/bfd.xml` je **prázdný výpis** (`<sessions>0</sessions>`, žádný `<bfd-session>`). To není vadná fixture — je to skutečný stav zařízení, na kterém BFD nakonfigurované je, ale BGP je Idle, takže žádná session nevznikla. Právě tenhle případ vede na SKIP.

- [ ] **Step 2: Napsat padající testy**

Create `tests/collectors/test_bfd.py`:

```python
"""Testy collectoru BFD proti nahranemu XML z laborky.

Fixture pro junos je zamerne PRAZDNY vypis: na vMX je BFD nakonfigurovane
u dvou sousedu, ale BGP je u obou Idle, takze zadna session nevznikla.
Prazdny vypis je platny stav, ne chyba sberu.
"""

from __future__ import annotations

import pytest

from migration_validator.collectors.bfd import BfdCollector

PLATFORMS = ("junos", "junos-evo")


@pytest.mark.parametrize("platform", PLATFORMS)
def test_returns_mapping_keyed_by_neighbor(rpc_fixture, platform):
    result = BfdCollector().parse(rpc_fixture(platform, "bfd"), platform)
    assert isinstance(result, dict)


def test_empty_output_is_a_valid_state(rpc_fixture):
    """Nula session neni selhani collectoru - collector nema co interpretovat."""
    result = BfdCollector().parse(rpc_fixture("junos", "bfd"), "junos")

    assert result == {}


def test_up_and_down_sessions_are_recorded_verbatim(rpc_fixture):
    result = BfdCollector().parse(rpc_fixture("junos-evo", "bfd"), "junos-evo")

    assert result["152.11.13.2"]["state"] == "Up"
    assert result["152.11.13.2"]["interface"] == "et-0/0/8.13"
    assert result["198.11.13.2"]["state"] == "Down"
    assert result["198.11.13.2"]["remote_state"] == "AdminDown"


def test_client_names_are_collected(rpc_fixture):
    """Klient rozlisi BGP session od te, kterou drzi jiny protokol."""
    result = BfdCollector().parse(rpc_fixture("junos-evo", "bfd"), "junos-evo")

    assert result["152.11.13.2"]["clients"] == ["BGP"]


def test_entries_have_expected_keys(rpc_fixture):
    result = BfdCollector().parse(rpc_fixture("junos-evo", "bfd"), "junos-evo")

    for data in result.values():
        assert set(data) == {
            "state",
            "interface",
            "remote_state",
            "local_diagnostic",
            "clients",
            "detection_time",
            "transmission_interval",
            "multiplier",
        }


def test_collector_passes_detail_flag(rpc_fixture):
    """Strucna varianta nema bfd-client ani remote-state."""
    assert BfdCollector().rpc_kwargs("junos") == {"detail": True}
```

- [ ] **Step 3: Spustit testy a ověřit, že padnou**

Run: `.venv/bin/pytest tests/collectors/test_bfd.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'migration_validator.collectors.bfd'`

- [ ] **Step 4: Implementovat collector**

Create `migration_validator/collectors/bfd.py`:

```python
"""Sber stavu BFD session.

Collector nerozhoduje, jestli je chybejici session problem - to zavisi na
tom, jestli je BFD vubec nakonfigurovane a jestli bezi BGP, a obojí vi az
check.

Pouziva detail variantu: strucny vypis nema ani bfd-client, ani
remote-state, a bez klienta nejde odlisit session drzenou BGP od jine.

Prazdny vypis je platny stav. Overeno proti laborce 2026-07-29: na vMX je
BFD nakonfigurovane u dvou sousedu, ale BGP je u obou Idle, takze session
je nula.
"""

from __future__ import annotations

from typing import Any

from lxml import etree

from migration_validator.collectors.base import Collector
from migration_validator.collectors.interfaces import _int, _text
from migration_validator.collectors.registry import register


@register
class BfdCollector(Collector):
    name = "bfd"

    def rpc_name(self, platform: str) -> str:
        return "get_bfd_session_information"

    def rpc_kwargs(self, platform: str) -> dict[str, Any]:
        return {"detail": True}

    def parse(self, xml: etree._Element, platform: str) -> dict[str, dict[str, Any]]:
        sessions: dict[str, dict[str, Any]] = {}

        for node in xml.iter("bfd-session"):
            neighbor = _text(node, "session-neighbor")
            if not neighbor:
                continue

            clients = []
            for client in node.iter("bfd-client"):
                name = _text(client, "client-name")
                if name:
                    clients.append(name)

            sessions[neighbor] = {
                "state": _text(node, "session-state") or "unknown",
                "interface": _text(node, "session-interface"),
                "remote_state": _text(node, "remote-state"),
                "local_diagnostic": _text(node, "local-diagnostic"),
                "clients": clients,
                "detection_time": _text(node, "session-detection-time"),
                "transmission_interval": _text(node, "session-transmission-interval"),
                "multiplier": _int(node, "session-adaptive-multiplier"),
            }

        return sessions
```

Do `migration_validator/collectors/all.py` přidat `bfd` do importu (abecedně první):

```python
from migration_validator.collectors import (  # noqa: F401
    arp,
    bfd,
    bgp,
    evpn,
    interfaces,
    nd,
    routes,
)
```

- [ ] **Step 5: Spustit testy a ověřit, že prochází**

Run: `.venv/bin/pytest tests/collectors/ -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add migration_validator/collectors/bfd.py migration_validator/collectors/all.py tests/collectors/test_bfd.py tests/fixtures/rpc/junos/bfd.xml tests/fixtures/rpc/junos-evo/bfd.xml
git commit -m "feat(collector): sbirat stav BFD session

Detail varianta kvuli bfd-client a remote-state; strucna ani jedno nema.
Prazdny vypis je platny stav - fixture pro junos je presne ten pripad."
```

---

## Task 7: Check `static_route_status`

**Files:**
- Create: `migration_validator/checks/routes.py`
- Modify: `migration_validator/checks/all.py`
- Create: `tests/checks/test_routes.py`

**Interfaces:**
- Consumes: `Check`, `CheckContext`, `Mode`, `Finding`, `Outcome`, `Severity`; `ctx.scope.selectors.static_routes`; `ctx.subject["routes"]`; `ctx.baseline["routes"]`
- Produces: `StaticRouteStatusCheck` s `id = "static_route_status"`, `label = "Staticka routa"`

- [ ] **Step 1: Napsat padající testy**

Create `tests/checks/test_routes.py`:

```python
"""Testy checku statickych rout.

Check na XML nesaha - fakta se skladaji rucne, protoze prave kombinace
'nakonfigurovano ale nenainstalovano' se z jedne nahravky vytahnout neda.
"""

from __future__ import annotations

import pytest

from migration_validator.checks.base import CheckContext
from migration_validator.checks.routes import StaticRouteStatusCheck
from migration_validator.config import default_config
from migration_validator.models.result import Outcome
from migration_validator.models.scope import Scope, ScopeKey, Selectors, device_scope

CONFIGURED = [
    {"rib": "inet.0", "prefix": "198.62.1.0/29", "next_hop": ["152.11.13.2"]},
]


def _scope(static_routes=None) -> Scope:
    return Scope(
        id="svc:CPE13:Internet",
        kind="service",
        key=ScopeKey(description="CPE13", service_type="Internet"),
        selectors=Selectors(static_routes=list(static_routes or [])),
    )


def _ctx(subject_routes, baseline_routes=None, scope=None) -> CheckContext:
    return CheckContext(
        scope=scope or _scope(CONFIGURED),
        subject={"routes": subject_routes},
        baseline={"routes": baseline_routes} if baseline_routes is not None else None,
        config=default_config(),
    )


def _installed(next_hop="152.11.13.2"):
    return {
        "inet.0": {
            "198.62.1.0/29": {
                "next_hop": [next_hop],
                "via": ["et-0/0/8.13"],
                "active": True,
            }
        }
    }


def test_installed_route_passes():
    findings = StaticRouteStatusCheck().run(_ctx(_installed(), _installed()))

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.OK
    assert findings[0].value == "152.11.13.2"


def test_configured_but_not_installed_is_broken():
    """Presne to, co laborka delala 2026-07-29: 5 statik v konfiguraci, 0 v tabulce."""
    findings = StaticRouteStatusCheck().run(_ctx({}, _installed()))

    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "neni v tabulce"
    assert findings[0].baseline_value == "152.11.13.2"


def test_changed_next_hop_is_degraded_and_carries_the_old_value():
    findings = StaticRouteStatusCheck().run(
        _ctx(_installed("152.11.13.9"), _installed("152.11.13.2"))
    )

    assert findings[0].outcome is Outcome.DEGRADED
    assert findings[0].value == "152.11.13.9"
    assert findings[0].baseline_value == "152.11.13.2"


def test_route_that_vanished_from_config_and_table_is_broken():
    """Routa vyrazena z konfigurace se do selektoru subjektu nedostane.

    Bez tretiho zdroje sjednoceni (mereni baseline) by se scope na jeji
    chybeni nikdy nezeptal a zmizela by beze stopy.
    """
    findings = StaticRouteStatusCheck().run(
        _ctx({}, _installed(), scope=_scope([]))
    )

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "chybi"


def test_new_route_passes():
    findings = StaticRouteStatusCheck().run(_ctx(_installed(), {}))

    assert findings[0].outcome is Outcome.OK
    assert findings[0].baseline_value is None


def test_family_comes_from_the_prefix_not_the_rib_name():
    """Bez rodiny by radek spadl do bezhlavickove sekce nad IPv4 i IPv6."""
    ctx = _ctx(
        {
            "inet6.0": {
                "2001:aaaa::/64": {
                    "next_hop": ["2001:abcd:11:13::b"],
                    "via": ["et-0/0/8.13"],
                    "active": True,
                }
            }
        },
        scope=_scope(
            [{"rib": "inet6.0", "prefix": "2001:aaaa::/64", "next_hop": []}]
        ),
    )

    findings = StaticRouteStatusCheck().run(ctx)

    assert findings[0].family == 6


def test_label_carries_rib_and_prefix():
    """Identita jde do popisku, next-hop je hodnota - jinak by ZMENA vypsala par dvakrat."""
    findings = StaticRouteStatusCheck().run(_ctx(_installed()))

    assert findings[0].label == "Staticka routa (inet.0 198.62.1.0/29)"


def test_service_without_static_routes_gets_no_row():
    """R-1: chybejici konfigurace se v bloku neprojevi vubec."""
    findings = StaticRouteStatusCheck().run(_ctx({}, {}, scope=_scope([])))

    assert findings == []


def test_device_scope_reports_state_without_intent():
    """Bez inventory neni zamer znam, takze se nehlasi 'nakonfigurovano a chybi'."""
    ctx = CheckContext(
        scope=device_scope(),
        subject={"routes": _installed()},
        baseline=None,
        config=default_config(),
    )

    findings = StaticRouteStatusCheck().run(ctx)

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.OK
```

- [ ] **Step 2: Spustit testy a ověřit, že padnou**

Run: `.venv/bin/pytest tests/checks/test_routes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'migration_validator.checks.routes'`

- [ ] **Step 3: Implementovat check**

Create `migration_validator/checks/routes.py`:

```python
"""Check statickych rout.

Iteruje pres sjednoceni tri zdroju (AR-14): konfigurace subjektu, mereni
subjektu a mereni baseline. Kazdy z nich zavira jednu diru:

- bez konfigurace by nesel poznat rozpor 'nakonfigurovano, neni v tabulce',
- bez mereni subjektu by v rezimu bez inventory nebylo co vypsat,
- bez mereni baseline by tise zmizelo vsechno, co migrace odstranila -
  routa vyrazena z konfigurace se do selektoru subjektu nedostane, takze
  by se scope na jeji chybeni nikdy nezeptal.

Identita routy je (RIB, prefix), next-hop je hodnota. Diky tomu se zmena
next-hopu cte jako zmenena routa - jeden radek se sloupcem ZMENA - ne jako
routa zmizela a jina pribyla.

Zapsany predpoklad: jmena RIB migraci prezijou. `_aligned_baseline_data`
v enginu preslovnuje mezi baseline a subjectem jen oblast `interfaces`;
`routes` jsou klicovane table -> prefix a preslovneni nedostanou. V laborce
to plati (L3VPN-CPE13-NNI.inet*.0 je na obou zarizenich stejne), ale sady
instanci se lisi - mgmt_junos je jen na jednom z nich. Kdyby budouci migrace
prejmenovala VRF, kazda routa v ni se precte jako chybejici + nova.
"""

from __future__ import annotations

import ipaddress
from typing import Any

from migration_validator.checks.base import Check, CheckContext, Mode
from migration_validator.checks.registry import register
from migration_validator.models.result import Finding, Outcome, Severity

MISSING_FROM_TABLE = "neni v tabulce"
MISSING_ENTIRELY = "chybi"


def prefix_family(prefix: str) -> int | None:
    """Rodina se odvozuje z prefixu, ne ze jmena RIB.

    Jmeno RIB rodinu obsahovat nemusi (bgp.l3vpn.0). Bez rodiny by radek
    spadl do bezhlavickove sekce nad IPv4 i IPv6 (FAMILY_ORDER).
    """
    try:
        return ipaddress.ip_network(prefix, strict=False).version
    except ValueError:
        return None


def _next_hop_text(data: dict[str, Any] | None) -> str | None:
    if data is None:
        return None
    next_hops = data.get("next_hop") or []
    return ", ".join(next_hops) if next_hops else "-"


def _flatten(routes: dict[str, Any] | None) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (table, prefix): data
        for table, prefixes in (routes or {}).items()
        for prefix, data in prefixes.items()
    }


@register
class StaticRouteStatusCheck(Check):
    id = "static_route_status"
    title = "Stav statickych rout"
    label = "Staticka routa"
    mode = Mode.BOTH
    requires = ("routes",)
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        configured = {
            (str(route.get("rib")), str(route.get("prefix")))
            for route in ctx.scope.selectors.static_routes
        }
        subject = _flatten(ctx.subject.get("routes"))
        baseline = _flatten((ctx.baseline or {}).get("routes"))

        findings = []
        for identity in sorted(configured | set(subject) | set(baseline)):
            findings.append(
                self._finding(
                    identity,
                    configured=identity in configured,
                    subject=subject.get(identity),
                    baseline=baseline.get(identity),
                    is_device=ctx.scope.is_device,
                )
            )
        return findings

    def _finding(
        self,
        identity: tuple[str, str],
        configured: bool,
        subject: dict[str, Any] | None,
        baseline: dict[str, Any] | None,
        is_device: bool,
    ) -> Finding:
        rib, prefix = identity
        label = f"{self.label} ({rib} {prefix})"
        family = prefix_family(prefix)
        was = _next_hop_text(baseline)

        if subject is None:
            # Bez inventory neni zamer znam, takze se rozpor nehlasi
            # (AR-17). Sem se v device scope dostane jen routa, ktera byla
            # v baseline a v subjektu neni.
            value = MISSING_FROM_TABLE if configured and not is_device else MISSING_ENTIRELY
            message = (
                f"{rib} {prefix}: nakonfigurovana, ale neni v routovaci tabulce"
                if value == MISSING_FROM_TABLE
                else f"{rib} {prefix}: v baseline byla, v subjektu neni"
            )
            return Finding(
                Outcome.BROKEN,
                message,
                label=label,
                family=family,
                value=value,
                baseline_value=was,
                baseline=baseline,
            )

        now = _next_hop_text(subject)

        if was is not None and was != now:
            return Finding(
                Outcome.DEGRADED,
                f"{rib} {prefix}: next-hop se zmenil {was} -> {now}",
                label=label,
                family=family,
                value=now,
                baseline_value=was,
                baseline=baseline,
                subject=subject,
            )

        return Finding(
            Outcome.OK,
            f"{rib} {prefix}: {now}",
            label=label,
            family=family,
            value=now,
            baseline_value=was,
            baseline=baseline,
            subject=subject,
        )
```

V `migration_validator/checks/all.py` nahradit řádek s importem:

```python
from migration_validator.checks import bgp, evpn, ifaces, reachability, routes  # noqa: F401
```

- [ ] **Step 4: Spustit testy a ověřit, že prochází**

Run: `.venv/bin/pytest tests/checks/test_routes.py -v`
Expected: PASS (9 testů)

- [ ] **Step 5: Commit**

```bash
git add migration_validator/checks/routes.py migration_validator/checks/all.py tests/checks/test_routes.py
git commit -m "feat(check): static_route_status - zamer proti tabulce i proti baseline

Iteruje pres sjednoceni konfigurace subjektu, mereni subjektu a mereni
baseline. Bez tretiho zdroje by routa vyrazena z konfigurace zmizela
beze stopy - do selektoru subjektu se nedostane, takze by se scope na
jeji chybeni nikdy nezeptal.

Identita je (RIB, prefix) a jde do popisku; next-hop je hodnota, aby
sloupec ZMENA vypsal 'bylo <next-hop>' a ne cely par dvakrat."
```

---

## Task 8: Check `bfd_session_state`

**Files:**
- Create: `migration_validator/checks/bfd.py`
- Modify: `migration_validator/checks/all.py`
- Create: `tests/checks/test_bfd.py`

**Interfaces:**
- Consumes: `ctx.scope.selectors.bfd_peers`; `ctx.subject["bfd"]`, `ctx.subject["bgp"]`; `ctx.baseline["bfd"]`; `peer_family` z `checks/bgp.py`
- Produces: `BfdSessionStateCheck` s `id = "bfd_session_state"`, `label = "BFD"`

- [ ] **Step 1: Napsat padající testy**

Create `tests/checks/test_bfd.py`:

```python
"""Testy checku BFD.

Matice stavu odpovida tomu, co laborka ukazovala 2026-07-29:
152.11.13.2 Up, 198.11.13.2 Down, 198.11.14.2 nakonfigurovane pres skupinu
ale bez session (BGP Idle), a par peeru bez BFD vubec.
"""

from __future__ import annotations

import pytest

from migration_validator.checks.base import CheckContext
from migration_validator.checks.bfd import BfdSessionStateCheck
from migration_validator.config import default_config
from migration_validator.models.result import Outcome
from migration_validator.models.scope import Scope, ScopeKey, Selectors, device_scope

INTENT = [{"peer": "198.11.13.2", "minimum_interval": 3000, "multiplier": 3, "source": "neighbor"}]


def _scope(bfd_peers=None, bgp_neighbors=("198.11.13.2",)) -> Scope:
    return Scope(
        id="svc:CPE13:IPVPN",
        kind="service",
        key=ScopeKey(description="CPE13", service_type="IPVPN"),
        selectors=Selectors(
            bgp_neighbors=list(bgp_neighbors),
            bfd_peers=list(bfd_peers if bfd_peers is not None else INTENT),
        ),
    )


def _ctx(sessions, bgp_state="Established", baseline_sessions=None, scope=None):
    return CheckContext(
        scope=scope or _scope(),
        subject={"bfd": sessions, "bgp": {"198.11.13.2": {"state": bgp_state}}},
        baseline=(
            {"bfd": baseline_sessions, "bgp": {}} if baseline_sessions is not None else None
        ),
        config=default_config(),
    )


def test_session_up_passes():
    findings = BfdSessionStateCheck().run(_ctx({"198.11.13.2": {"state": "Up"}}))

    assert findings[0].outcome is Outcome.OK
    assert findings[0].value == "Up"
    assert findings[0].label == "BFD (198.11.13.2)"


def test_session_down_is_broken():
    findings = BfdSessionStateCheck().run(_ctx({"198.11.13.2": {"state": "Down"}}))

    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "Down"


def test_missing_session_with_established_bgp_is_broken():
    """BGP bezi, BFD se nedomluvilo - realna degradace doby vypadku."""
    findings = BfdSessionStateCheck().run(_ctx({}))

    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "bez session"


def test_missing_session_with_idle_bgp_is_skipped():
    """BFD nemuze nabehnout, dokud nebezi BGP.

    Bez teto vetve by L3VPN-CPE14-UNI dostalo dva FAIL radky za jednu
    pricinu a v ostrem behu by se to opakovalo u kazde nedojete sluzby.
    """
    findings = BfdSessionStateCheck().run(_ctx({}, bgp_state="Idle"))

    assert findings[0].outcome is Outcome.SKIP
    assert findings[0].value == "BGP neni Established"


def test_bfd_removed_since_baseline_is_broken():
    findings = BfdSessionStateCheck().run(
        _ctx({}, baseline_sessions={"198.11.13.2": {"state": "Up"}}, scope=_scope(bfd_peers=[]))
    )

    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "BFD odstraneno"
    assert findings[0].baseline_value == "Up"


def test_session_without_intent_is_degraded():
    """Detektor diry v pruchodu hierarchii BFD.

    Kdyby check iteroval jen pres zamer, chyba v dedeni ze skupiny by se
    schovala pred vystupem nastroje.
    """
    findings = BfdSessionStateCheck().run(
        _ctx({"198.11.13.2": {"state": "Up"}}, scope=_scope(bfd_peers=[]))
    )

    assert findings[0].outcome is Outcome.DEGRADED
    assert findings[0].value == "bez konfigurace"


def test_service_without_bfd_gets_no_row():
    """R-1: bez konfigurace, bez session a bez session v baseline - zadny radek."""
    findings = BfdSessionStateCheck().run(_ctx({}, scope=_scope(bfd_peers=[])))

    assert findings == []


def test_family_comes_from_peer_address():
    findings = BfdSessionStateCheck().run(
        _ctx(
            {"2001:db8:11:14::b": {"state": "Up"}},
            scope=_scope(
                bfd_peers=[{"peer": "2001:db8:11:14::b", "source": "group"}],
                bgp_neighbors=("2001:db8:11:14::b",),
            ),
        )
    )

    assert findings[0].family == 6


def test_device_scope_reports_state_without_intent():
    """Bez inventory se nehlasi ani 'bez session', ani 'bez konfigurace'."""
    ctx = CheckContext(
        scope=device_scope(),
        subject={"bfd": {"152.11.13.2": {"state": "Up"}}, "bgp": {}},
        baseline=None,
        config=default_config(),
    )

    findings = BfdSessionStateCheck().run(ctx)

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.OK
```

- [ ] **Step 2: Spustit testy a ověřit, že padnou**

Run: `.venv/bin/pytest tests/checks/test_bfd.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'migration_validator.checks.bfd'`

- [ ] **Step 3: Implementovat check**

Create `migration_validator/checks/bfd.py`:

```python
"""Check stavu BFD session.

Iteruje pres sjednoceni tri zdroju (AR-14): zamer z konfigurace, session
v subjektu a session v baseline. Peer, ktery BFD nikdy nemel, radek
nedostane (R-1).

Vazba na stav BGP je zamerna, ne kosmeticka: BFD nemuze nabehnout, dokud
nebezi BGP, takze bez ni by sluzba se spadlym BGP dostala dva FAIL radky
za jednu pricinu. V ostrem behu by se to opakovalo u kazde nedojete
sluzby a operator by si zvykl vypis preskakovat.
"""

from __future__ import annotations

from typing import Any

from migration_validator.checks.base import Check, CheckContext, Mode
from migration_validator.checks.bgp import ESTABLISHED, peer_family
from migration_validator.checks.registry import register
from migration_validator.models.result import Finding, Outcome, Severity

NO_SESSION = "bez session"
BGP_NOT_UP = "BGP neni Established"
REMOVED = "BFD odstraneno"
NO_INTENT = "bez konfigurace"


@register
class BfdSessionStateCheck(Check):
    id = "bfd_session_state"
    title = "Stav BFD session"
    label = "BFD"
    mode = Mode.BOTH
    requires = ("bfd", "bgp")
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        intent = {
            str(item.get("peer")): item for item in ctx.scope.selectors.bfd_peers
        }
        sessions: dict[str, Any] = ctx.subject.get("bfd", {})
        baseline_sessions: dict[str, Any] = (ctx.baseline or {}).get("bfd", {})
        bgp: dict[str, Any] = ctx.subject.get("bgp", {})

        findings = []
        for peer in sorted(set(intent) | set(sessions) | set(baseline_sessions)):
            findings.append(
                self._finding(
                    peer,
                    configured=peer in intent,
                    session=sessions.get(peer),
                    baseline=baseline_sessions.get(peer),
                    bgp_state=str((bgp.get(peer) or {}).get("state", "")),
                    is_device=ctx.scope.is_device,
                )
            )
        return findings

    def _finding(
        self,
        peer: str,
        configured: bool,
        session: dict[str, Any] | None,
        baseline: dict[str, Any] | None,
        bgp_state: str,
        is_device: bool,
    ) -> Finding:
        label = f"{self.label} ({peer})"
        family = peer_family(peer)
        was = str(baseline.get("state")) if baseline else None

        if session is not None:
            state = str(session.get("state", "unknown"))

            # Bez inventory neni zamer znam, takze se nehlasi, ze
            # konfigurace chybi (AR-17).
            if not configured and not is_device:
                return Finding(
                    Outcome.DEGRADED,
                    f"{peer}: session existuje ({state}), v konfiguraci sluzby neni",
                    label=label,
                    family=family,
                    value=NO_INTENT,
                    baseline_value=was,
                    subject=session,
                )

            outcome = Outcome.OK if state == "Up" else Outcome.BROKEN
            return Finding(
                outcome,
                f"{peer}: session {state}",
                label=label,
                family=family,
                value=state,
                baseline_value=was,
                baseline=baseline,
                subject=session,
            )

        if not configured:
            # Session byla v baseline, v subjektu neni ani zamer.
            # Migrace nema tise shodit ze stolu ochranu, ktera tam byla.
            return Finding(
                Outcome.BROKEN,
                f"{peer}: BFD bylo v baseline ({was}), v subjektu neni nakonfigurovane",
                label=label,
                family=family,
                value=REMOVED,
                baseline_value=was,
                baseline=baseline,
            )

        if bgp_state != ESTABLISHED:
            return Finding(
                Outcome.SKIP,
                f"{peer}: BFD nakonfigurovano, ale BGP je {bgp_state or 'neznamy'}",
                label=label,
                family=family,
                value=BGP_NOT_UP,
                baseline_value=was,
            )

        return Finding(
            Outcome.BROKEN,
            f"{peer}: BFD nakonfigurovano, BGP bezi, ale session neexistuje",
            label=label,
            family=family,
            value=NO_SESSION,
            baseline_value=was,
        )
```

V `migration_validator/checks/all.py` nahradit řádek s importem (`bfd` abecedně
před `bgp`; `checks/bfd.py` importuje `checks/bgp.py` kvůli `peer_family`
a `ESTABLISHED`, což je bezpečné — `bgp` na `bfd` nesahá, cyklus nevzniká):

```python
from migration_validator.checks import bfd, bgp, evpn, ifaces, reachability, routes  # noqa: F401
```

- [ ] **Step 4: Spustit testy a ověřit, že prochází**

Run: `.venv/bin/pytest tests/checks/test_bfd.py -v`
Expected: PASS (9 testů)

- [ ] **Step 5: Commit**

```bash
git add migration_validator/checks/bfd.py migration_validator/checks/all.py tests/checks/test_bfd.py
git commit -m "feat(check): bfd_session_state se sjednocenim zameru, mereni a baseline

Chybejici session pri BGP mimo Established je SKIP, ne FAIL: BFD nemuze
nabehnout, dokud nebezi BGP, takze by sluzba dostala dva FAIL radky za
jednu pricinu.

Session bez zameru je WARN - detektor diry v dedeni BFD hierarchie.
Bez nej by chyba v pruchodu byla neviditelna z vystupu nastroje."
```

---

## Task 9: `unassigned` v enginu

**Files:**
- Modify: `migration_validator/engine.py:167-179` (nové funkce vedle `_unassigned_bgp_peers`), `:247`
- Modify: `migration_validator/models/result.py:192-193`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `Snapshot.facts["routes"]`, `Snapshot.facts["bfd"]`, `Scope.selectors`
- Produces: `RunResult.unassigned` s klíči `bgp_peers`, `static_routes`, `bfd_sessions`

- [ ] **Step 1: Napsat padající testy**

Do `tests/test_engine.py` přidat. Testy se drží idiomu, který soubor už používá
pro `bgp_peers` (`test_unassigned_bgp_peers_are_reported` na `:206`): snímek
vyrobí `_new()` / `_old()` a fakta se do něj dopisují přímo.

```python
MGMT_ROUTE = {
    "mgmt_junos.inet.0": {
        "0.0.0.0/0": {"next_hop": ["10.0.0.2"], "via": ["fxp0.0"], "active": True}
    }
}

SERVICE_ROUTE = {
    "inet.0": {
        "198.62.1.0/29": {
            "next_hop": ["152.11.13.2"],
            "via": ["et-0/0/8.13"],
            "active": True,
        }
    }
}


def test_management_static_route_lands_in_unassigned():
    """Statika v mgmt_junos padne na fxp0.0, ze ktere se scope nikdy nestane.

    Do inventory se nedostane. Kdyby ji nezachytil unassigned, zmizela by
    z vystupu uplne.
    """
    subject = _new()
    subject.facts["routes"] = MGMT_ROUTE

    result = api.evaluate(subject, baseline=_old(), now=NOW)

    assert result.unassigned["static_routes"] == [
        {
            "rib": "mgmt_junos.inet.0",
            "prefix": "0.0.0.0/0",
            "next_hop": ["10.0.0.2"],
            "via": ["fxp0.0"],
            "snapshot": "subject",
        }
    ]


def test_route_claimed_by_a_scope_is_not_unassigned():
    subject = _new()
    subject.facts["routes"] = SERVICE_ROUTE
    subject.scopes[0].selectors.static_routes = [
        {"rib": "inet.0", "prefix": "198.62.1.0/29", "next_hop": ["152.11.13.2"]}
    ]

    result = api.evaluate(subject, baseline=_old(), now=NOW)

    assert result.unassigned["static_routes"] == []


def test_bfd_session_of_unknown_peer_lands_in_unassigned():
    subject = _new()
    subject.facts["bfd"] = {"10.1.1.1": {"state": "Up", "interface": "et-0/0/9.0"}}

    result = api.evaluate(subject, baseline=_old(), now=NOW)

    assert result.unassigned["bfd_sessions"] == [
        {
            "peer": "10.1.1.1",
            "interface": "et-0/0/9.0",
            "state": "Up",
            "snapshot": "subject",
        }
    ]


def test_bfd_session_of_known_peer_is_not_unassigned():
    subject = _new()
    subject.facts["bfd"] = {"10.1.1.1": {"state": "Up", "interface": "et-0/0/9.0"}}
    subject.scopes[0].selectors.bgp_neighbors = ["10.1.1.1"]

    result = api.evaluate(subject, baseline=_old(), now=NOW)

    assert result.unassigned["bfd_sessions"] == []


def test_device_scope_reports_nothing_as_unassigned():
    """Device scope propousti vsechno, takze nic neprirazene byt nemuze."""
    subject = _new()
    subject.facts["routes"] = MGMT_ROUTE
    subject.facts["bfd"] = {"10.1.1.1": {"state": "Up", "interface": "et-0/0/9.0"}}
    subject.scopes = []

    result = api.evaluate(subject, now=NOW)

    assert result.unassigned["static_routes"] == []
    assert result.unassigned["bfd_sessions"] == []
```

Pokud `_new()` nepřijímá snímek bez `scopes`, ověř, že `_scopes_of()` v enginu
spadne na `device_scope()` — to je právě ta větev, kterou poslední test měří.

- [ ] **Step 2: Spustit testy a ověřit, že padnou**

Run: `.venv/bin/pytest tests/test_engine.py -v -k unassigned`
Expected: FAIL — `KeyError: 'static_routes'`

- [ ] **Step 3: Implementovat v `engine.py`**

Za `_unassigned_bgp_peers` (`:167-179`) vložit:

```python
def _unassigned_static_routes(
    subject: Snapshot, scopes: list[Scope]
) -> list[dict[str, Any]]:
    """Routy z tabulky, ktere si nenarokuje zadny scope.

    Sem spadne statika v management instanci - fxp0.0 se scopem nikdy
    nestane, takze routa nema ke ktere sluzbe patrit. A taky routa, kterou
    parser neumel precist: kdyz konfiguracni tvar nezname, do selektoru se
    nedostane, ale v tabulce ji videt je. Je to tedy i pojistka proti
    mezeram v parsovani.
    """
    assigned = {
        (str(route.get("rib")), str(route.get("prefix")))
        for scope in scopes
        for route in scope.selectors.static_routes
    }
    if any(scope.is_device for scope in scopes):
        return []
    return [
        {
            "rib": table,
            "prefix": prefix,
            "next_hop": data.get("next_hop", []),
            "via": data.get("via", []),
            "snapshot": "subject",
        }
        for table, prefixes in sorted((subject.facts.get("routes") or {}).items())
        for prefix, data in sorted(prefixes.items())
        if (table, prefix) not in assigned
    ]


def _unassigned_bfd_sessions(
    subject: Snapshot, scopes: list[Scope]
) -> list[dict[str, Any]]:
    """Session peeru, ktery neni v zadnem bgp_neighbors.

    Napriklad BFD drzene jinym klientem nez BGP - parser takovy zamer
    necte, takze by session jinak nikde nefigurovala.
    """
    assigned = {peer for scope in scopes for peer in scope.selectors.bgp_neighbors}
    if any(scope.is_device for scope in scopes):
        return []
    return [
        {
            "peer": peer,
            "interface": data.get("interface"),
            "state": data.get("state"),
            "snapshot": "subject",
        }
        for peer, data in sorted((subject.facts.get("bfd") or {}).items())
        if peer not in assigned
    ]
```

A `:247` nahradit:

```python
        unassigned={
            "bgp_peers": _unassigned_bgp_peers(subject, subject_scopes),
            "static_routes": _unassigned_static_routes(subject, subject_scopes),
            "bfd_sessions": _unassigned_bfd_sessions(subject, subject_scopes),
        },
```

V `models/result.py:192-193`:

```python
    unassigned: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: {
            "bgp_peers": [],
            "static_routes": [],
            "bfd_sessions": [],
        }
    )
```

- [ ] **Step 4: Spustit testy a ověřit, že prochází**

Run: `.venv/bin/pytest tests/test_engine.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add migration_validator/engine.py migration_validator/models/result.py tests/test_engine.py
git commit -m "feat(engine): unassigned o staticke routy a BFD session

Mechanikou shodne s bgp_peers. Chyti mgmt statiku, ktera nema ke ktere
sluzbe patrit, i routu, kterou parser neumel precist - je to tedy
zaroven pojistka proti mezeram v parsovani.

Zustava strojovy vystup, stejne jako bgp_peers: sekce NESPAROVANO
v textovem reportu vypisuje unmatched, ne unassigned."
```

---

## Task 10: Regenerace fixtures, conformance, dokumentace a ostré ověření

**Files:**
- Modify: `172.20.20.4.yml`, `172.20.20.5.yml` (kořen), `tests/fixtures/172.20.20.4.yml`, `tests/fixtures/172.20.20.5.yml`
- Modify: `tests/collectors/test_conformance.py`
- Modify: `docs/cs/files/collectors.md`, `docs/cs/files/checks.md`, `docs/cs/files/parsers.md`, `docs/cs/files/models.md`, `docs/cs/reference.md`, `docs/cs/README.md` a jejich anglické protějšky v `docs/en/`

**Interfaces:**
- Consumes: vše z Tasků 1–9
- Produces: zelená sada a ostře ověřený běh

- [ ] **Step 1: Regenerovat inventory z laborky**

CLI parserů se na heslo ptá přes `getpass.getpass()` (`mx_parser.py:1961`), což
se v dávkovém běhu zasekne. Použij proto driver, který volá tytéž funkce přímo:

```bash
eval "$(grep '^export MIG_LAB_PASSWORD=' ~/.bashrc)"
.venv/bin/python - <<'EOF'
import importlib.util
import os
import sys
from pathlib import Path

import yaml

PASSWORD = os.environ["MIG_LAB_PASSWORD"]

TARGETS = (
    ("mx_parser.py", "172.20.20.4", "JunosServiceParser"),
    ("evo_parser.py", "172.20.20.5", "JunosEvoAcxServiceParser"),
)

for filename, host, class_name in TARGETS:
    spec = importlib.util.spec_from_file_location(Path(filename).stem, filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[Path(filename).stem] = module
    spec.loader.exec_module(module)

    options = module.ConnectionOptions(
        hostname=host, username="admin", auth_type="password", password=PASSWORD
    )
    device = module.build_device(options)
    device.open()
    try:
        config = module.retrieve_configuration(device)
        services = getattr(module, class_name)(config).parse()
    finally:
        device.close()

    data = module.create_yaml_data(hostname=host, services=services)
    Path(f"{host}.yml").write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    print(f"ulozeno {host}.yml  ({len(services)} sluzeb)")
EOF
```

Pokud `main()` zapisuje YAML jinými parametry `yaml.safe_dump` (odsazení, řazení
klíčů), převezmi je z něj — jinak se regenerovaná inventory bude proti té staré
lišit v každém řádku a diff nepůjde přečíst.

Ověř, že výstup obsahuje nová pole a verzi 3:

```bash
grep -n "schema_version" 172.20.20.4.yml 172.20.20.5.yml
grep -c "static_route:" 172.20.20.5.yml
grep -A 4 "  bfd:" 172.20.20.5.yml | head -20
```

Expected: `schema_version: 3` v obou. Na `.5` čtyři služby s neprázdným `bfd`
(z toho dvě se `source: group`) a služby v `L3VPN-CPE13-NNI` i v globální
instanci s neprázdným `static_route`.

- [ ] **Step 2: Zkopírovat inventory do fixtures**

```bash
cp 172.20.20.4.yml tests/fixtures/172.20.20.4.yml
cp 172.20.20.5.yml tests/fixtures/172.20.20.5.yml
```

- [ ] **Step 3: Rozšířit conformance test o nové collectory**

V `tests/collectors/test_conformance.py` do importů a do `COLLECTORS`:

```python
from migration_validator.collectors.bfd import BfdCollector
from migration_validator.collectors.routes import RoutesCollector

COLLECTORS = (
    InterfacesCollector(),
    ArpCollector(),
    NdCollector(),
    BgpCollector(),
    EvpnVpwsCollector(),
    EvpnEsiCollector(),
    EvpnMacCollector(),
    RoutesCollector(),
    BfdCollector(),
)
```

Tenhle test je jediné místo, kde se skutečné collectory potkají se skutečnými checky nad daty z laborky. Kdyby collector přejmenoval klíč, check by jinak tiše vracel SKIP.

- [ ] **Step 4: Spustit celou sadu**

Run: `.venv/bin/pytest`
Expected: PASS. Padající testy z Tasku 3 (kvůli `schema_version 2` ve fixtures) teď musí být zelené. Porovnej se seznamem, který sis zapsal v Tasku 3, Step 7.

- [ ] **Step 5: Pořídit čerstvé snímky z laborky**

Staré snímky (`runs/ipv6/`, `runs/ipv6-live-2026-07-29/`) mají `schema_version 2` a nejdou přehrát. Nový capture:

```bash
eval "$(grep '^export MIG_LAB_PASSWORD=' ~/.bashrc)"
.venv/bin/python -m migration_validator.cli capture \
  --device 172.20.20.4 --inventory 172.20.20.4.yml --phase pre-migration \
  --username admin --auth password --password "$MIG_LAB_PASSWORD" \
  --output runs/bfd-static-2026-07-29/pre.json
.venv/bin/python -m migration_validator.cli capture \
  --device 172.20.20.5 --inventory 172.20.20.5.yml --phase post-migration \
  --username admin --auth password --password "$MIG_LAB_PASSWORD" \
  --output runs/bfd-static-2026-07-29/post.json
```

- [ ] **Step 6: Ostré ověření reportu**

```bash
.venv/bin/python -m migration_validator.cli evaluate \
  --snapshot runs/bfd-static-2026-07-29/post.json \
  --baseline runs/bfd-static-2026-07-29/pre.json --detail
```

Zkontroluj proti tomu, co laborka skutečně měla 2026-07-29:

| očekávaný řádek | proč |
|---|---|
| `FAIL \| BFD (198.11.13.2) : Down` | session je Down, `remote-state AdminDown` |
| `PASS \| BFD (152.11.13.2) : Up` | jediná zdravá session |
| `SKIP \| BFD (198.11.14.2) : BGP neni Established` | BFD ze skupiny CPE14, BGP Idle |
| `SKIP \| BFD (2001:db8:11:14::b) : BGP neni Established` | IPv6 peer dědí BFD ze skupiny |
| statické routy na `.5` jako **PASS s prázdným sloupcem ZMENA** | v tabulce jsou, ale v `pre` nebyly, takže `baseline_value` je `None` |
| žádný řádek BFD u služeb bez BFD | R-1 |

- [ ] **Step 6b: Ověřit AR-15 na straně `pre` — to je hlavní důkaz této vlny**

Report ze subjectu ukazuje zdravý stav. Rozpor mezi konfigurací a tabulkou
je vidět jen na `.4`, kde je konfigurace plná a tabulka prázdná:

```bash
.venv/bin/python -m migration_validator.cli evaluate \
  --snapshot runs/bfd-static-2026-07-29/pre.json --detail | grep "Staticka routa"
```

Expected: **čtyři řádky `FAIL` s hodnotou `neni v tabulce`** — `198.62.1.0/29`
a `2001:aaaa::/64` v `inet.0` / `inet6.0`, `172.26.1.0/29` a `2001:eeee::/64`
v `L3VPN-CPE13-NNI.*`. Mgmt statiky mezi nimi **nejsou** (nemapují se na
službu), zato musí být v `unassigned.static_routes`:

```bash
.venv/bin/python -m migration_validator.cli evaluate \
  --snapshot runs/bfd-static-2026-07-29/pre.json --format json \
  | .venv/bin/python -c "import json,sys; print(json.dumps(json.load(sys.stdin)['unassigned']['static_routes'], indent=2))"
```

Expected: dvě položky, `mgmt_junos.inet.0 0.0.0.0/0` a `mgmt_junos.inet6.0 ::/0`,
obě s `via: ["fxp0.0"]`.

Pokud tyhle čtyři FAIL řádky nevyjdou, **nespoléhej na to, že je laborka jinak
nakonfigurovaná** — zkontroluj nejdřív `_assign_static_routes` a selektor
`static_routes` ve scope. Rozpor mezi konfigurací a tabulkou je jediný důvod,
proč AR-15 vznikl, a je to jediné místo, kde ho jde ověřit proti živému
zařízení.

Zkontroluj i strojový výstup:

```bash
.venv/bin/python -m migration_validator.cli evaluate \
  --snapshot runs/bfd-static-2026-07-29/post.json --format json \
  | .venv/bin/python -c "import json,sys; print(json.dumps(json.load(sys.stdin)['unassigned'], indent=2))"
```

Expected: `static_routes` a `bfd_sessions` jsou přítomné klíče. Na `.5` by měly být prázdné (mgmt statika tam v tabulce není); na `.4` by `static_routes` mělo obsahovat obě `mgmt_junos` routy — ověř tím, že spustíš totéž nad `pre.json`.

- [ ] **Step 7: Změřit šířku bloku**

```bash
.venv/bin/python -m migration_validator.cli evaluate \
  --snapshot runs/bfd-static-2026-07-29/post.json \
  --baseline runs/bfd-static-2026-07-29/pre.json --detail \
  | awk '{ print length }' | sort -rn | head -3
```

Spec počítá s tím, že popisek statické routy je nejširší v reportu (`Staticka routa (L3VPN-CPE13-NNI.inet6.0 2001:eeee::/64)` = 54 znaků). Vlna 1 srazila nejširší řádek na 149; pokud teď výrazně překročí 180, zapiš to jako nález pro F-14, neřeš to v této vlně.

- [ ] **Step 8: Aktualizovat dokumentaci**

Ve **stejném commitu** jako kód (pravidlo z vlny 1). Projít a doplnit:

- `docs/cs/files/collectors.md` — `routes.py` a `bfd.py`, jejich RPC a tvar faktů
- `docs/cs/files/checks.md` — `static_route_status` a `bfd_session_state` včetně tabulek stavů
- `docs/cs/files/parsers.md` — `static_route` a `bfd` v inventory, dědění BFD hierarchií, normalizace jména RIB, `routing-options` ve filtru
- `docs/cs/files/models.md` — `Selectors.static_routes` / `.bfd_peers`, `FACT_AREAS`
- `docs/cs/reference.md` — **obě** zvýšené verze, ne jen jedna: `schema_version` inventory 2 → 3 **a** `schema_version` snímku 2 → 3. Druhá je ta, kvůli které přestanou jít přehrát `runs/ipv6/` a `runs/ipv6-live-2026-07-29/`, a ten důsledek patří do uživatelské dokumentace. Dále nové klíče `unassigned` (`static_routes`, `bfd_sessions`) a nová pole `static_route` / `bfd` v inventory
- `docs/cs/README.md` — pokud vyjmenovává, co nástroj kontroluje
- Totéž v `docs/en/`

Ukázky výstupu v dokumentaci **generuj z fixtures**, nepiš ručně — vlna 1 ukázala, že ručně psané se rozejdou.

- [ ] **Step 9: Spustit celou sadu naposled**

Run: `.venv/bin/pytest`
Expected: PASS, žádný přeskočený test navíc oproti výchozímu stavu (1 skipped).

- [ ] **Step 10: Commit**

```bash
git add 172.20.20.4.yml 172.20.20.5.yml tests/fixtures/ tests/collectors/test_conformance.py docs/
git commit -m "chore: regenerovat inventory a fixtures na schema 3, dopsat dokumentaci

Conformance test dostal oba nove collectory - je to jedine misto, kde se
skutecny collector potka se skutecnym checkem nad daty z laborky.

Ostre overeno proti laborce: BFD 198.11.13.2 Down, 152.11.13.2 Up,
oba peery skupiny CPE14 SKIP kvuli BGP Idle."
```

---

## Poznámky pro implementaci

**Pořadí volání v `parse()` není libovolné.** Výsledné pořadí musí být:

```python
    def parse(self) -> list[InterfaceService]:
        self._parse_routing_instances()      # naplni self.routing_instances vcetne .bfd
        self.static_routes = self._parse_static_routes()
        self._parse_default_bgp_neighbors()  # naplni i self.default_bfd
        ...
        self._assign_bgp_neighbors(services, interface_configs_by_name)
        self._assign_static_routes(services, interface_configs_by_name)
        self._assign_bfd(services)           # potrebuje uz naplnene bgp_neighbor
```

`_assign_bfd` spuštěné dřív než `_assign_bgp_neighbors` tiše nic nepřiřadí — seznam peerů je prázdný a žádný test to nemusí chytit, protože výsledek je „služba bez BFD", což je legitimní stav.

**`unassigned` se do textového reportu nevypisuje.** Sekce `NESPAROVANO` v `text_report.py:296` vypisuje `result.unmatched`, ne `result.unassigned`. `bgp_peers` je dnes jen ve strojovém výstupu a nové klíče to následují. Nepřidávej rendering — spec ho nepředepisuje a byla by to změna nad rámec zadání.

**Když `evaluate` po Tasku 4 spadne na verzi snímku**, je to očekávané až do Tasku 10, Step 5. Do té doby ověřuj proti fixtures, ne proti `runs/`.

**F-2 (podřádky se jménem RIB) se v této vlně nedělá.** Jméno RIB jde do kvalifikátoru popisku. Pokud se při implementaci ukáže, že popisky jsou nepohodlně dlouhé, je to vstup pro vlnu 3, ne důvod měnit renderer teď.

**`rpc_kwargs` je zapojené, ale `routes` a `bfd` jsou jeho první uživatelé.** `collectors/base.py:66` volá `rpc(**self.rpc_kwargs(platform))`, takže `{"protocol": "static"}` i `{"detail": True}` se na RPC dostanou — ověřeno při psaní plánu. Všechny stávající collectory vracejí `{}`, takže dosud ten hook nikdo nepoužil. Test `test_collector_passes_detail_flag` ověřuje jen kontrakt metody, ne zapojení; kdyby se `collect()` někdy přepsalo, tenhle test to nechytí a BFD by tiše sbíral stručný výpis bez `remote-state`. Zapojení hlídá až conformance test z Tasku 10.
