# IPv4/IPv6 separace a nový report — implementační plán

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rozdělit IPv4 a IPv6 v celém řetězci od parserů po report, zprovoznit ping na IPv6 přes ND tabulku, a nahradit řídký textový výstup blokem na službu se sekcemi po rodinách.

**Architecture:** Rodina je datové pole (`family`) nesené `PingTarget` a `Finding`, ne vlastnost odvozovaná z textu adresy. Adresy se rozdělí už v parserech (`ipv4_address` / `ipv6_address`) a rozdělené projdou přes `ServiceEntry` a `Selectors` až do `ScopeResult.identity`, odkud je čte renderer. Renderer se rozpadá na `reporting/view.py` (data) a `reporting/text_report.py` (sazba).

**Tech Stack:** Python 3.11+, `jnpr.junos` (PyEZ), `lxml`, `PyYAML`, `pytest`. Spouštění vždy přes `.venv/bin/python` a `.venv/bin/pytest`.

**Spec:** [docs/superpowers/specs/2026-07-28-ipv6-a-report-design.md](../specs/2026-07-28-ipv6-a-report-design.md)

## Global Constraints

- **Oba parsery se mění v zámku.** `evo_parser.py` a `mx_parser.py` jsou dva téměř identické soubory (liší se 146 řádky, tato změna se jich netýká). Každá změna v jednom musí být provedena i ve druhém. Čísla řádků v plánu jsou z `evo_parser.py`; v `mx_parser.py` sedí s posunem do čtyř řádků.
- **Jazyk kódu a výstupu:** komentáře, docstringy, jména a všechny uživatelské řetězce v `migration_validator/` jsou **česky bez diakritiky**. Dokumentace v `docs/` je česky **s diakritikou**. Parsery (`evo_parser.py`, `mx_parser.py`) mají české komentáře **s diakritikou** — v nich se drží jejich stávající styl.
- **Collector nikdy neinterpretuje.** Vrací syrová strukturovaná data, žádné verdikty. Kritéria patří do checků.
- **Checky nikdy nesahají na síť.** `migration_validator/checks/` nesmí importovat nic z `migration_validator.connection`.
- **Nový obor faktů, který je seznam, musí být zapsán na tři místa:** `models/scope.py:FACT_AREAS`, `models/scope.py:_empty()` a `capture.py:LIST_AREAS`. Vynechání `_empty()` způsobí, že selhaný collector vrátí `{}` místo `[]`.
- **Testy se spouští z kořene repozitáře:** `.venv/bin/pytest`.
- **Laboratoř:** `172.20.20.4` (vMX, platforma `junos`), `172.20.20.5` (PTX10002-36QDD, platforma `junos-evo`). Uživatel `admin`, autentizace heslem. Heslo je v `~/.bashrc` pod non-interactive guardem, načíst explicitně:
  ```bash
  eval "$(grep '^export MIG_LAB_PASSWORD=' ~/.bashrc)"
  ```

## File Structure

| soubor | odpovědnost | task |
|---|---|---|
| `evo_parser.py`, `mx_parser.py` | rozdělení adres na rodiny, `schema_version: 2` ve výstupu | 1 |
| `tests/parsers/test_family_split.py` | *nový* — offline testy parserů nad vloženým XML | 1, 2 |
| `migration_validator/models/inventory.py` | `ServiceEntry` se čtyřmi poli adres, kontrola `schema_version` | 3 |
| `migration_validator/models/scope.py` | `Selectors` po rodinách, obor `nd` | 4, 6 |
| `migration_validator/scoping/builder.py` | napojení nových selektorů | 4 |
| `migration_validator/models/snapshot.py` | `SCHEMA_VERSION` 1 → 2 | 4 |
| `migration_validator/models/result.py` | `Finding` / `CheckResult` o `family`, `value`, `baseline_value`, `delta`; `ScopeResult.identity` | 5 |
| `migration_validator/checks/base.py` | propagace nových polí v `run_check()` | 5 |
| `migration_validator/engine.py` | plnění `identity` | 5 |
| `migration_validator/collectors/nd.py` | *nový* — sběr ND tabulky | 6 |
| `migration_validator/checks/reachability.py` | `nd_present`, rodiny a MAC v ARP/ND, rodina u pingu | 7 |
| `migration_validator/probes/ping.py` | `rapid`, cíle a zdroje po rodinách, link-local, v6 fallback | 8 |
| `migration_validator/collectors/bgp.py` | prefixy po RIB | 9 |
| `migration_validator/checks/bgp.py` | rodina peeru, porovnání po RIB, `value` | 9 |
| `migration_validator/checks/ifaces.py` | jemnější findingy, `value` / `baseline_value` / `delta` | 10 |
| `migration_validator/reporting/view.py` | *nový* — `ScopeResult` → `ServiceView`, čistá data | 11 |
| `migration_validator/reporting/text_report.py` | sazba: šířky, sekce, sbalování | 12 |

`reporting/view.py` je oddělený od sazby proto, že pořadí sekcí a zařazení řádků do rodin jde testovat porovnáním datových struktur, zatímco sazbu je nutné testovat proti řetězci s mezerami. Míchat obojí v jednom souboru by znamenalo testovat logiku přes mezery.

---

## Task 1: Rozdělení adres na rodiny v obou parserech

**Files:**
- Modify: `evo_parser.py:118-131` (`InterfaceConfig`), `:133-155` (`InterfaceService`), `:855-872` (`_build_interface_config` návrat), `:918-935` (`_classify_interface` návrat), `:1494-1550`, `:1670-1680`, `:1776-1817` (`create_yaml_data`, `clean_service_dict`)
- Modify: `mx_parser.py` — tytéž změny (posun do čtyř řádků)
- Create: `tests/parsers/__init__.py`, `tests/parsers/test_family_split.py`

**Interfaces:**
- Consumes: nic (první task)
- Produces: YAML inventory se `schema_version: 2` a klíči `ipv4_address: list[str]`, `ipv6_address: list[str]`, `virtual_gw_ipv4_address: list[str]`, `virtual_gw_ipv6_address: list[str]` místo `ip_address` a `virtual_gw_ip_address`. Dataclass `InterfaceConfig` má pole `ipv4_addresses`, `ipv6_addresses`, `virtual_gw_ipv4_addresses`, `virtual_gw_ipv6_addresses` (množné číslo). `InterfaceService` má pole v jednotném čísle podle YAML klíčů. Obě třídy jsou v obou parserech; jméno parseru je `JunosEvoAcxServiceParser` v `evo_parser.py` a `JunosServiceParser` v `mx_parser.py`, konstruktor obou bere `etree._Element` s kořenem konfigurace.

- [ ] **Step 1: Napiš padající test rozdělení rodin**

Vytvoř `tests/parsers/__init__.py` (prázdný soubor) a `tests/parsers/test_family_split.py`:

```python
"""Offline testy parseru - konfigurace se vklada jako XML, laborka neni potreba.

Oba parsery se meni v zamku, takze kazdy test bezi proti obema.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from lxml import etree

ROOT = Path(__file__).resolve().parents[2]


def _load(module_name: str, filename: str):
    """Parsery jsou skripty v korenu repozitare, ne balicek - nacteme je podle cesty."""
    spec = importlib.util.spec_from_file_location(module_name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


evo = _load("evo_parser_under_test", "evo_parser.py")
mx = _load("mx_parser_under_test", "mx_parser.py")

PARSERS = (
    pytest.param(evo, evo.JunosEvoAcxServiceParser, id="evo"),
    pytest.param(mx, mx.JunosServiceParser, id="mx"),
)

DUAL_STACK = """
<configuration>
  <interfaces>
    <interface>
      <name>ge-0/0/2</name>
      <unit>
        <name>13</name>
        <description>INTERNET-CPE13-NNI</description>
        <family>
          <inet>
            <address><name>152.11.13.1/30</name></address>
          </inet>
          <inet6>
            <address><name>2001:abcd:11:13::a/127</name></address>
          </inet6>
        </family>
      </unit>
    </interface>
  </interfaces>
</configuration>
"""


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_families_are_separate_fields(module, parser_class):
    services = parser_class(etree.fromstring(DUAL_STACK)).parse()
    unit = next(s for s in services if s.interface == "ge-0/0/2.13")

    assert unit.ipv4_address == ["152.11.13.1/30"]
    assert unit.ipv6_address == ["2001:abcd:11:13::a/127"]


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_merged_field_is_gone(module, parser_class):
    """Slite pole nesmi prezit - jinak by se na nej necekane navazalo."""
    services = parser_class(etree.fromstring(DUAL_STACK)).parse()
    unit = next(s for s in services if s.interface == "ge-0/0/2.13")

    assert not hasattr(unit, "ip_address")
```

- [ ] **Step 2: Spusť test a ověř, že padá**

Run: `.venv/bin/pytest tests/parsers/test_family_split.py -v`
Expected: FAIL — `AttributeError: 'InterfaceService' object has no attribute 'ipv4_address'`

- [ ] **Step 3: Rozděl pole v `InterfaceConfig`**

V obou parserech nahraď v `InterfaceConfig` (`evo_parser.py:128-129`):

```python
    ip_addresses: list[str] = field(default_factory=list)
    virtual_gw_ip_addresses: list[str] = field(default_factory=list)
```

za:

```python
    ipv4_addresses: list[str] = field(default_factory=list)
    ipv6_addresses: list[str] = field(default_factory=list)
    virtual_gw_ipv4_addresses: list[str] = field(default_factory=list)
    virtual_gw_ipv6_addresses: list[str] = field(default_factory=list)
```

- [ ] **Step 4: Rozděl pole v `InterfaceService`**

V obou parserech nahraď v `InterfaceService` (`evo_parser.py:138-139`):

```python
    ip_address: list[str]
    virtual_gw_ip_address: list[str]
```

za:

```python
    ipv4_address: list[str]
    ipv6_address: list[str]
    virtual_gw_ipv4_address: list[str]
    virtual_gw_ipv6_address: list[str]
```

- [ ] **Step 5: Přestaň slévat rodiny při stavbě `InterfaceConfig`**

V obou parserech nahraď návrat v `_build_interface_config` (`evo_parser.py:865-871`):

```python
            ip_addresses=unique(
                ipv4_addresses + ipv6_addresses
            ),
            virtual_gw_ip_addresses=unique(
                virtual_gw_ipv4_addresses
                + virtual_gw_ipv6_addresses
            ),
```

za:

```python
            ipv4_addresses=unique(ipv4_addresses),
            ipv6_addresses=unique(ipv6_addresses),
            virtual_gw_ipv4_addresses=unique(virtual_gw_ipv4_addresses),
            virtual_gw_ipv6_addresses=unique(virtual_gw_ipv6_addresses),
```

Lokální proměnné `ipv4_addresses`, `ipv6_addresses`, `virtual_gw_ipv4_addresses` a `virtual_gw_ipv6_addresses` už se počítají zvlášť na řádcích 811–841 — ty se nemění.

- [ ] **Step 6: Předej rozdělená pole do `InterfaceService`**

V obou parserech nahraď v `_classify_interface` (`evo_parser.py:923-924`):

```python
            ip_address=interface.ip_addresses,
            virtual_gw_ip_address=interface.virtual_gw_ip_addresses,
```

za:

```python
            ipv4_address=interface.ipv4_addresses,
            ipv6_address=interface.ipv6_addresses,
            virtual_gw_ipv4_address=interface.virtual_gw_ipv4_addresses,
            virtual_gw_ipv6_address=interface.virtual_gw_ipv6_addresses,
```

- [ ] **Step 7: Sjednoť zbývající konzumenty slitých seznamů**

Najdi v obou parserech všechna zbývající použití starých jmen:

```bash
grep -n "ip_addresses\|virtual_gw_ip_addresses\|\.ip_address\b" evo_parser.py mx_parser.py
```

Zbývají tři místa (čísla z `evo_parser.py`). Přepiš je tak, aby braly obě rodiny.

Řádek ~1498 a ~1501 — podmínky uvnitř detekce služby:

```python
        if interface.ipv4_addresses or interface.ipv6_addresses:
```

```python
        if interface.virtual_gw_ipv4_addresses or interface.virtual_gw_ipv6_addresses:
```

Řádek ~1542 — kombinovaná podmínka:

```python
            interface.ipv4_addresses
            or interface.ipv6_addresses
            or interface.virtual_gw_ipv4_addresses
            or interface.virtual_gw_ipv6_addresses
```

Řádek ~1673 — podmínka „rozhraní nemá žádnou adresu":

```python
            and not interface.ipv4_addresses
            and not interface.ipv6_addresses
            and not interface.virtual_gw_ipv4_addresses
            and not interface.virtual_gw_ipv6_addresses
```

Zkontroluj přesné okolní podmínky v souboru a zachovej jejich logiku — mění se jen zdroj adres, ne význam.

- [ ] **Step 8: Uprav pořadí klíčů v YAML výstupu**

V obou parserech nahraď v `clean_service_dict` (`evo_parser.py:1802-1803`):

```python
        "ip_address",
        "virtual_gw_ip_address",
```

za:

```python
        "ipv4_address",
        "ipv6_address",
        "virtual_gw_ipv4_address",
        "virtual_gw_ipv6_address",
```

- [ ] **Step 9: Přidej `schema_version` do výstupu**

V obou parserech uprav `create_yaml_data` (`evo_parser.py:1776`):

```python
INVENTORY_SCHEMA_VERSION = 2


def create_yaml_data(
    hostname: str,
    services: list[InterfaceService],
) -> dict[str, Any]:
    return {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "device": hostname,
        "interfaces": [
            clean_service_dict(asdict(service))
            for service in services
        ],
    }
```

Konstantu `INVENTORY_SCHEMA_VERSION` umísti nad funkci `create_yaml_data`.

- [ ] **Step 10: Spusť testy a ověř, že prochází**

Run: `.venv/bin/pytest tests/parsers/test_family_split.py -v`
Expected: PASS (4 testy — dva testy × dva parsery)

- [ ] **Step 11: Ověř, že se oba parsery pořád importují**

Run: `.venv/bin/python -c "import importlib.util,pathlib; [importlib.util.spec_from_file_location(n, p).loader.exec_module(importlib.util.module_from_spec(importlib.util.spec_from_file_location(n, p))) for n, p in (('a','evo_parser.py'), ('b','mx_parser.py'))]; print('ok')"`
Expected: `ok`

- [ ] **Step 12: Commit**

```bash
git add evo_parser.py mx_parser.py tests/parsers/
git commit -m "feat(parsers): split IPv4 and IPv6 addresses into separate fields

Both parsers already computed the families separately and then merged
them; they now keep them apart all the way into the YAML, which is what
everything downstream needs to know which family it is looking at.

Inventory output gains schema_version: 2."
```

---

## Task 2: Testy shody rodin u BGP sousedů

**Files:**
- Modify: `tests/parsers/test_family_split.py`
- Read only: `evo_parser.py:1116-1138`, `mx_parser.py:1120-1142`

**Interfaces:**
- Consumes: `InterfaceConfig.ipv4_addresses` / `.ipv6_addresses` z Tasku 1
- Produces: nic nového — jen pojistka pro spec 2

**Kontext:** `_bgp_neighbor_matches_interface` shodu rodin **už kontroluje** (`neighbor_ip.version == interface_address.version`, `evo_parser.py:1132-1136`). Tento task tedy nic neopravuje — jen na chování napíše testy, protože ve druhé spec na téže funkci poběží i mapování next-hopů statických rout a BFD. Jediná změna kódu je zdroj adres.

- [ ] **Step 1: Napiš padající test**

Přidej do `tests/parsers/test_family_split.py`:

```python
DUAL_STACK_BGP = """
<configuration>
  <interfaces>
    <interface>
      <name>ge-0/0/2</name>
      <unit>
        <name>13</name>
        <description>INTERNET-CPE13-NNI</description>
        <family>
          <inet>
            <address><name>152.11.13.1/30</name></address>
          </inet>
          <inet6>
            <address><name>2001:abcd:11:13::a/127</name></address>
          </inet6>
        </family>
      </unit>
    </interface>
  </interfaces>
  <protocols>
    <bgp>
      <group>
        <name>CPE</name>
        <type>external</type>
        <neighbor><name>152.11.13.2</name></neighbor>
        <neighbor><name>2001:abcd:11:13::b</name></neighbor>
      </group>
    </bgp>
  </protocols>
</configuration>
"""


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_bgp_neighbors_of_both_families_map_to_service(module, parser_class):
    services = parser_class(etree.fromstring(DUAL_STACK_BGP)).parse()
    unit = next(s for s in services if s.interface == "ge-0/0/2.13")

    assert set(unit.bgp_neighbor) == {"152.11.13.2", "2001:abcd:11:13::b"}


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_neighbor_outside_subnet_does_not_map(module, parser_class):
    """Cizi soused se nesmi prilepit ke sluzbe jen proto, ze je stejne rodiny."""
    config = DUAL_STACK_BGP.replace(
        "<neighbor><name>152.11.13.2</name></neighbor>",
        "<neighbor><name>10.99.99.2</name></neighbor>",
    )
    services = parser_class(etree.fromstring(config)).parse()
    unit = next(s for s in services if s.interface == "ge-0/0/2.13")

    assert "10.99.99.2" not in unit.bgp_neighbor
```

- [ ] **Step 2: Spusť test a ověř, že padá**

Run: `.venv/bin/pytest tests/parsers/test_family_split.py -v -k bgp`
Expected: FAIL — `_bgp_neighbor_matches_interface` iteruje `interface.ip_addresses`, které po Tasku 1 neexistuje, takže spadne na `AttributeError`

- [ ] **Step 3: Napoj shodu na obě rodiny**

V obou parserech nahraď v `_bgp_neighbor_matches_interface` (`evo_parser.py:1126`):

```python
        for address in interface.ip_addresses:
```

za:

```python
        for address in interface.ipv4_addresses + interface.ipv6_addresses:
```

Kontrola shody rodin uvnitř cyklu (`neighbor_ip.version == interface_address.version`) zůstává beze změny — právě ona zajišťuje, že se v6 soused nespáruje s v4 adresou.

- [ ] **Step 4: Spusť testy a ověř, že prochází**

Run: `.venv/bin/pytest tests/parsers/ -v`
Expected: PASS (8 testů)

- [ ] **Step 5: Commit**

```bash
git add evo_parser.py mx_parser.py tests/parsers/test_family_split.py
git commit -m "test(parsers): pin BGP neighbour-to-service mapping per family

The family check was already there; these tests hold it in place because
the next spec runs static-route next-hops and BFD through the same
function."
```

---

## Task 3: `ServiceEntry` po rodinách a kontrola verze inventory

**Files:**
- Modify: `migration_validator/models/inventory.py`
- Modify: `tests/models/test_inventory.py`

**Interfaces:**
- Consumes: YAML formát z Tasku 1
- Produces: `ServiceEntry` s poli `ipv4_address: list[str]`, `ipv6_address: list[str]`, `virtual_gw_ipv4_address: list[str]`, `virtual_gw_ipv6_address: list[str]`. `load_inventory(path) -> Inventory` vyhodí `ValueError` na `schema_version` různém od 2. Konstanta `INVENTORY_SCHEMA_VERSION = 2` v tomtéž modulu.

- [ ] **Step 1: Napiš padající testy**

Přidej do `tests/models/test_inventory.py`:

```python
import pytest

from migration_validator.models.inventory import ServiceEntry, load_inventory


def test_entry_keeps_families_apart():
    entry = ServiceEntry.from_dict(
        {
            "interface": "ge-0/0/2.13",
            "service_type": "Internet",
            "ipv4_address": ["152.11.13.1/30"],
            "ipv6_address": ["2001:abcd:11:13::a/127"],
            "virtual_gw_ipv4_address": ["152.11.13.254"],
            "virtual_gw_ipv6_address": [],
        }
    )

    assert entry.ipv4_address == ["152.11.13.1/30"]
    assert entry.ipv6_address == ["2001:abcd:11:13::a/127"]
    assert entry.virtual_gw_ipv4_address == ["152.11.13.254"]
    assert entry.virtual_gw_ipv6_address == []


def test_roundtrip_through_dict():
    data = {
        "interface": "ge-0/0/2.13",
        "service_type": "Internet",
        "ipv4_address": ["152.11.13.1/30"],
        "ipv6_address": ["2001:abcd:11:13::a/127"],
        "virtual_gw_ipv4_address": [],
        "virtual_gw_ipv6_address": [],
    }
    entry = ServiceEntry.from_dict(data)

    for key, value in data.items():
        assert entry.to_dict()[key] == value


def test_old_inventory_fails_loudly(tmp_path):
    """Bez verze by stara inventory tise prisla o adresy a sluzby by svitily zelene."""
    path = tmp_path / "old.yml"
    path.write_text(
        "device: 172.20.20.4\n"
        "interfaces:\n"
        "  - interface: ge-0/0/2.13\n"
        "    service_type: Internet\n"
        "    ip_address: ['152.11.13.1/30']\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="schema_version"):
        load_inventory(path)


def test_current_inventory_loads(tmp_path):
    path = tmp_path / "new.yml"
    path.write_text(
        "schema_version: 2\n"
        "device: 172.20.20.4\n"
        "interfaces:\n"
        "  - interface: ge-0/0/2.13\n"
        "    service_type: Internet\n"
        "    ipv4_address: ['152.11.13.1/30']\n",
        encoding="utf-8",
    )

    inventory = load_inventory(path)

    assert inventory.device == "172.20.20.4"
    assert inventory.entries[0].ipv4_address == ["152.11.13.1/30"]
```

- [ ] **Step 2: Spusť testy a ověř, že padají**

Run: `.venv/bin/pytest tests/models/test_inventory.py -v`
Expected: FAIL — `TypeError: ServiceEntry.__init__() got an unexpected keyword argument`

- [ ] **Step 3: Uprav `ServiceEntry`**

V `migration_validator/models/inventory.py` nahraď v dataclassu `ServiceEntry`:

```python
    ip_address: list[str] = field(default_factory=list)
    virtual_gw_ip_address: list[str] = field(default_factory=list)
```

za:

```python
    ipv4_address: list[str] = field(default_factory=list)
    ipv6_address: list[str] = field(default_factory=list)
    virtual_gw_ipv4_address: list[str] = field(default_factory=list)
    virtual_gw_ipv6_address: list[str] = field(default_factory=list)
```

V `from_dict` nahraď:

```python
            ip_address=_as_list(data.get("ip_address")),
            virtual_gw_ip_address=_as_list(data.get("virtual_gw_ip_address")),
```

za:

```python
            ipv4_address=_as_list(data.get("ipv4_address")),
            ipv6_address=_as_list(data.get("ipv6_address")),
            virtual_gw_ipv4_address=_as_list(data.get("virtual_gw_ipv4_address")),
            virtual_gw_ipv6_address=_as_list(data.get("virtual_gw_ipv6_address")),
```

V `to_dict` nahraď:

```python
            "ip_address": list(self.ip_address),
            "virtual_gw_ip_address": list(self.virtual_gw_ip_address),
```

za:

```python
            "ipv4_address": list(self.ipv4_address),
            "ipv6_address": list(self.ipv6_address),
            "virtual_gw_ipv4_address": list(self.virtual_gw_ipv4_address),
            "virtual_gw_ipv6_address": list(self.virtual_gw_ipv6_address),
```

- [ ] **Step 4: Přidej kontrolu verze do `load_inventory`**

Nad `load_inventory` přidej konstantu a rozšiř funkci:

```python
INVENTORY_SCHEMA_VERSION = 2


def load_inventory(path: str | Path) -> Inventory:
    """Nacte YAML vystup parseru konfigurace.

    Stara inventory se odmita, ne dopocitava. Pole adres se prejmenovala na
    rodiny; tolerantni cteni by u starsiho souboru tise vratilo sluzby bez
    adres, takze by neprobehl ping a sluzba by presto svitila zelene.
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: ocekavan YAML mapping, nalezeno {type(raw).__name__}")

    version = raw.get("schema_version")
    if version != INVENTORY_SCHEMA_VERSION:
        raise ValueError(
            f"{path}: inventory ma schema_version {version}, nastroj umi "
            f"{INVENTORY_SCHEMA_VERSION} - vygeneruj ji znovu parserem"
        )

    if "interfaces" not in raw:
        raise ValueError(f"{path}: chybi klic 'interfaces'")
    entries = [ServiceEntry.from_dict(item) for item in raw["interfaces"] or []]
    return Inventory(device=str(raw.get("device", "")), entries=entries)
```

- [ ] **Step 5: Spusť testy a ověř, že prochází**

Run: `.venv/bin/pytest tests/models/test_inventory.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add migration_validator/models/inventory.py tests/models/test_inventory.py
git commit -m "feat(inventory): per-family address fields, reject old schema

Tolerant reading would hand back services with no addresses at all, so a
stale inventory would skip every ping and still report green. Refusing to
load it is the honest failure."
```

---

## Task 4: `Selectors` po rodinách, `builder` a verze snapshotu

**Files:**
- Modify: `migration_validator/models/scope.py` (`Selectors`)
- Modify: `migration_validator/scoping/builder.py:70-80`
- Modify: `migration_validator/models/snapshot.py:17`
- Modify: `tests/conftest.py`
- Modify: `tests/models/test_scope.py`, `tests/scoping/test_builder.py`

**Interfaces:**
- Consumes: `ServiceEntry.ipv4_address` / `.ipv6_address` / `.virtual_gw_ipv4_address` / `.virtual_gw_ipv6_address` z Tasku 3
- Produces: `Selectors` s poli `local_ipv4: list[str]`, `local_ipv6: list[str]`, `virtual_gw_v4: list[str]`, `virtual_gw_v6: list[str]` (pole `local_addresses` a `virtual_gw` zanikají). `models/snapshot.py:SCHEMA_VERSION == 2`.

- [ ] **Step 1: Napiš padající testy**

Přidej do `tests/scoping/test_builder.py`:

```python
from migration_validator.models.inventory import Inventory, ServiceEntry
from migration_validator.scoping.builder import build_scopes


def test_selectors_keep_families_apart():
    inventory = Inventory(
        device="172.20.20.5",
        entries=[
            ServiceEntry(
                interface="et-0/0/8.13",
                service_type="Internet",
                description="INTERNET-CPE13-NNI",
                ipv4_address=["152.11.13.1/30"],
                ipv6_address=["2001:abcd:11:13::a/127"],
                virtual_gw_ipv4_address=["152.11.13.254"],
                virtual_gw_ipv6_address=[],
            )
        ],
    )

    scope = build_scopes(inventory)[0]

    assert scope.selectors.local_ipv4 == ["152.11.13.1/30"]
    assert scope.selectors.local_ipv6 == ["2001:abcd:11:13::a/127"]
    assert scope.selectors.virtual_gw_v4 == ["152.11.13.254"]
    assert scope.selectors.virtual_gw_v6 == []
```

Přidej do `tests/models/test_scope.py`:

```python
from migration_validator.models.scope import Selectors


def test_selectors_survive_roundtrip():
    selectors = Selectors(
        interfaces=["et-0/0/8.13"],
        local_ipv4=["152.11.13.1/30"],
        local_ipv6=["2001:abcd:11:13::a/127"],
        virtual_gw_v4=["152.11.13.254"],
        virtual_gw_v6=["2001:abcd:11:13::1"],
    )

    restored = Selectors.from_dict(selectors.to_dict())

    assert restored.local_ipv4 == ["152.11.13.1/30"]
    assert restored.local_ipv6 == ["2001:abcd:11:13::a/127"]
    assert restored.virtual_gw_v4 == ["152.11.13.254"]
    assert restored.virtual_gw_v6 == ["2001:abcd:11:13::1"]
```

Přidej do `tests/models/test_snapshot.py`:

```python
import pytest

from migration_validator.models.snapshot import (
    SCHEMA_VERSION,
    Snapshot,
    SnapshotVersionError,
)


def test_snapshot_version_is_two():
    assert SCHEMA_VERSION == 2


def test_old_snapshot_fails_loudly():
    with pytest.raises(SnapshotVersionError, match="schema_version"):
        Snapshot.from_dict({"schema_version": 1, "device": {"address": "x"}})
```

- [ ] **Step 2: Spusť testy a ověř, že padají**

Run: `.venv/bin/pytest tests/scoping/test_builder.py tests/models/test_scope.py tests/models/test_snapshot.py -v`
Expected: FAIL — `TypeError: Selectors.__init__() got an unexpected keyword argument 'local_ipv4'`

- [ ] **Step 3: Rozděl pole v `Selectors`**

V `migration_validator/models/scope.py` nahraď v dataclassu `Selectors`:

```python
    local_addresses: list[str] = field(default_factory=list)
    virtual_gw: list[str] = field(default_factory=list)
```

za:

```python
    local_ipv4: list[str] = field(default_factory=list)
    local_ipv6: list[str] = field(default_factory=list)
    virtual_gw_v4: list[str] = field(default_factory=list)
    virtual_gw_v6: list[str] = field(default_factory=list)
```

A v `to_dict`:

```python
            "local_ipv4": list(self.local_ipv4),
            "local_ipv6": list(self.local_ipv6),
            "virtual_gw_v4": list(self.virtual_gw_v4),
            "virtual_gw_v6": list(self.virtual_gw_v6),
```

`from_dict` se nemění — odvozuje klíče z `cls().to_dict()`.

- [ ] **Step 4: Napoj `builder`**

V `migration_validator/scoping/builder.py` nahraď uvnitř `Selectors(...)`:

```python
                    local_addresses=list(entry.ip_address),
                    virtual_gw=list(entry.virtual_gw_ip_address),
```

za:

```python
                    local_ipv4=list(entry.ipv4_address),
                    local_ipv6=list(entry.ipv6_address),
                    virtual_gw_v4=list(entry.virtual_gw_ipv4_address),
                    virtual_gw_v6=list(entry.virtual_gw_ipv6_address),
```

- [ ] **Step 5: Zvedni verzi snapshotu**

V `migration_validator/models/snapshot.py:17`:

```python
SCHEMA_VERSION = 2
```

- [ ] **Step 6: Sjednoť testovací conftest**

V `tests/conftest.py` uvnitř `synthetic_snapshot` nahraď:

```python
                "source": scope.selectors.local_addresses[0].split("/")[0],
```

za:

```python
                "source": scope.selectors.local_ipv4[0].split("/")[0],
```

a v podmínce pod tím:

```python
            if scope.selectors.bgp_neighbors and scope.selectors.local_ipv4
```

- [ ] **Step 7: Najdi zbylá použití starých jmen**

Run: `grep -rn "local_addresses\|selectors.virtual_gw\b" migration_validator tests`
Expected: jediný zásah je `migration_validator/probes/ping.py` — ten opraví Task 8. Cokoliv dalšího oprav teď.

- [ ] **Step 8: Spusť testy a ověř, že prochází**

Run: `.venv/bin/pytest tests/models tests/scoping -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add migration_validator/models/scope.py migration_validator/models/snapshot.py migration_validator/scoping/builder.py tests/
git commit -m "feat(scope): split selector addresses by family, bump snapshot schema

Ping picks its source address as local_addresses[0]; over a merged list
the family of that source is arbitrary, so IPv6 ping cannot be fixed
without the split reaching this far."
```

---

## Task 5: `family` / `value` / `baseline_value` / `delta` a `ScopeResult.identity`

**Files:**
- Modify: `migration_validator/models/result.py` (`Finding`, `CheckResult`, `ScopeResult`)
- Modify: `migration_validator/checks/base.py:126-139` (`run_check`)
- Modify: `migration_validator/engine.py` (`_run_scope`)
- Modify: `tests/models/test_result.py`, `tests/checks/test_base.py`

**Interfaces:**
- Consumes: `Selectors` z Tasku 4
- Produces: `Finding(outcome, message, label=None, family=None, value=None, baseline_value=None, delta=None, baseline=None, subject=None, details={})` — `family` je `int | None` s hodnotami `None`, `4`, `6`. `CheckResult` má tatáž čtyři pole a serializuje je v `to_dict()` jen když nejsou `None`. `ScopeResult.identity: dict[str, Any]` s klíči `description`, `service_type`, `service_subtype`, `routing_instance`, `ipv4`, `ipv6`, `virtual_gw_v4`, `virtual_gw_v6`.

- [ ] **Step 1: Napiš padající testy**

Přidej do `tests/models/test_result.py`:

```python
from migration_validator.models.result import (
    CheckResult,
    Finding,
    Outcome,
    ScopeResult,
    Severity,
    Status,
)


def test_finding_carries_presentation_fields():
    finding = Finding(
        Outcome.OK,
        "rozhrani je up/up",
        label="Interface admin status",
        family=4,
        value="Up",
        baseline_value="Up",
        delta=None,
    )

    assert finding.family == 4
    assert finding.value == "Up"
    assert finding.baseline_value == "Up"


def test_check_result_omits_empty_presentation_fields():
    result = CheckResult(
        id="interface_state",
        mode="state",
        status=Status.PASS,
        severity=Severity.CRITICAL,
        message="up/up",
    )

    payload = result.to_dict()

    assert "family" not in payload
    assert "value" not in payload


def test_check_result_serialises_presentation_fields():
    result = CheckResult(
        id="interface_traffic",
        mode="both",
        status=Status.PASS,
        severity=Severity.ADVISORY,
        message="v toleranci",
        family=6,
        value="460 pps",
        baseline_value="520 pps",
        delta="-12 %",
    )

    payload = result.to_dict()

    assert payload["family"] == 6
    assert payload["value"] == "460 pps"
    assert payload["baseline_value"] == "520 pps"
    assert payload["delta"] == "-12 %"


def test_scope_result_serialises_identity():
    scope = ScopeResult(
        scope_id="svc:X:Internet",
        key={"description": "X", "service_type": "Internet"},
        status=Status.PASS,
        match=None,
        identity={
            "description": "X",
            "service_type": "Internet",
            "service_subtype": None,
            "routing_instance": None,
            "ipv4": ["152.11.13.1/30"],
            "ipv6": [],
            "virtual_gw_v4": [],
            "virtual_gw_v6": [],
        },
    )

    assert scope.to_dict()["identity"]["ipv4"] == ["152.11.13.1/30"]
```

Přidej do `tests/checks/test_base.py`:

```python
from migration_validator.checks.base import Check, CheckContext, Mode, run_check
from migration_validator.config import default_config
from migration_validator.models.result import Finding, Outcome, Severity
from migration_validator.models.scope import device_scope


class _PresentationCheck(Check):
    id = "presentation_probe"
    title = "Testovaci check"
    mode = Mode.STATE

    def run(self, ctx):
        return [
            Finding(
                Outcome.OK,
                "hotovo",
                label="Neco",
                family=6,
                value="Up",
                baseline_value="Down",
                delta="zmena",
            )
        ]


def test_run_check_propagates_presentation_fields():
    ctx = CheckContext(
        scope=device_scope(),
        subject={},
        baseline=None,
        config=default_config(),
    )

    result = run_check(_PresentationCheck(), ctx)[0]

    assert result.family == 6
    assert result.value == "Up"
    assert result.baseline_value == "Down"
    assert result.delta == "zmena"
```

Pozn.: `_PresentationCheck` se **neregistruje** dekorátorem `@register` — je jen pro tento test a registrace by ho pustila do všech ostatních běhů.

- [ ] **Step 2: Spusť testy a ověř, že padají**

Run: `.venv/bin/pytest tests/models/test_result.py tests/checks/test_base.py -v`
Expected: FAIL — `TypeError: Finding.__init__() got an unexpected keyword argument 'family'`

- [ ] **Step 3: Rozšiř `Finding`**

V `migration_validator/models/result.py` nahraď dataclass `Finding`:

```python
@dataclass
class Finding:
    """Namereny vysledek jednoho checku pred odvozenim statusu.

    `message` je duvod pro sbaleny radek reportu. `value`, `baseline_value`
    a `delta` jsou to, co se tiskne ve sloupcich rozbaleneho bloku - rozklad
    na popisek a hodnotu musi udelat check, protoze jen on vi, co je u dane
    veliciny hodnota a co vysvetleni.
    """

    outcome: Outcome
    message: str
    label: str | None = None
    family: int | None = None
    value: str | None = None
    baseline_value: str | None = None
    delta: str | None = None
    baseline: dict[str, Any] | None = None
    subject: dict[str, Any] | None = None
    details: dict[str, Any] = field(default_factory=dict)
```

- [ ] **Step 4: Rozšiř `CheckResult`**

V témže souboru přidej do dataclassu `CheckResult` za pole `label`:

```python
    family: int | None = None
    value: str | None = None
    baseline_value: str | None = None
    delta: str | None = None
```

A v `CheckResult.to_dict()` za blok s `label`:

```python
        for name in ("family", "value", "baseline_value", "delta"):
            attribute = getattr(self, name)
            if attribute is not None:
                payload[name] = attribute
```

- [ ] **Step 5: Přidej `identity` do `ScopeResult`**

V témže souboru rozšiř `ScopeResult`:

```python
@dataclass
class ScopeResult:
    scope_id: str
    key: dict[str, Any]
    status: Status
    match: MatchInfo | None
    checks: list[CheckResult] = field(default_factory=list)
    identity: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope_id": self.scope_id,
            "key": self.key,
            "identity": self.identity,
            "status": self.status.value,
            "match": self.match.to_dict() if self.match else None,
            "checks": [check.to_dict() for check in self.checks],
        }
```

- [ ] **Step 6: Propaguj pole v `run_check`**

V `migration_validator/checks/base.py` doplň do konstrukce `CheckResult` na konci `run_check` za `label=finding.label`:

```python
            family=finding.family,
            value=finding.value,
            baseline_value=finding.baseline_value,
            delta=finding.delta,
```

- [ ] **Step 7: Naplň `identity` v enginu**

V `migration_validator/engine.py` přidej pomocnou funkci nad `_run_scope`:

```python
def _identity(scope: Scope) -> dict[str, Any]:
    """Vse, co report o sluzbe vypisuje - jinak by to zustalo ve scope.

    Renderer nema pristup ke scopum, jen k vysledku, takze bez tohoto by
    sloupce s adresami, virtual gateway a routing-instanci nemel odkud vzit.
    """
    key = scope.key
    selectors = scope.selectors
    return {
        "description": key.description if key else None,
        "service_type": key.service_type if key else None,
        "service_subtype": key.service_subtype if key else None,
        "routing_instance": (
            selectors.routing_instances[0] if selectors.routing_instances else None
        ),
        "ipv4": list(selectors.local_ipv4),
        "ipv6": list(selectors.local_ipv6),
        "virtual_gw_v4": list(selectors.virtual_gw_v4),
        "virtual_gw_v6": list(selectors.virtual_gw_v6),
    }
```

A v `_run_scope` doplň do návratu `ScopeResult(...)`:

```python
        identity=_identity(scope),
```

- [ ] **Step 8: Spusť testy a ověř, že prochází**

Run: `.venv/bin/pytest tests/models tests/checks/test_base.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add migration_validator/models/result.py migration_validator/checks/base.py migration_validator/engine.py tests/
git commit -m "feat(result): carry family and display values through to the report

Checks return a Czech sentence today, but the new report needs
label/value columns and a delta. Only the check knows whether a value is
a number and what a drop in it means, so it produces those fields rather
than the renderer parsing them back out.

ScopeResult gains identity so addresses, VGW and routing-instance reach
the renderer at all."
```

---

## Task 6: Collector ND

**Files:**
- Create: `migration_validator/collectors/nd.py`
- Create: `tests/collectors/test_nd.py`
- Create: `tests/fixtures/rpc/junos/nd.xml`, `tests/fixtures/rpc/junos-evo/nd.xml` (nahrané z laborky)
- Modify: `migration_validator/collectors/all.py`
- Modify: `migration_validator/models/scope.py` (`FACT_AREAS`, `_empty`, `Scope.select`)
- Modify: `migration_validator/capture.py` (`LIST_AREAS`)
- Modify: `tests/collectors/test_conformance.py`, `tests/conftest.py`

**Interfaces:**
- Consumes: `Collector` base z `migration_validator/collectors/base.py`
- Produces: `NdCollector` s `name = "nd"`, `rpc_name()` vracející `"get_ipv6_nd_information"`, `parse()` vracející `list[dict]` se **čtyřmi klíči**: `ip`, `mac`, `interface`, `state`. Obor faktů `"nd"` je seznam.

- [ ] **Step 1: Nahraj fixtures z laborky**

```bash
eval "$(grep '^export MIG_LAB_PASSWORD=' ~/.bashrc)"
.venv/bin/python - <<'PY'
import os
from pathlib import Path
from jnpr.junos import Device
from lxml import etree

TARGETS = {"junos": "172.20.20.4", "junos-evo": "172.20.20.5"}
for platform, host in TARGETS.items():
    device = Device(host=host, user="admin", passwd=os.environ["MIG_LAB_PASSWORD"])
    device.open()
    xml = device.rpc.get_ipv6_nd_information()
    out = Path("tests/fixtures/rpc") / platform / "nd.xml"
    out.write_bytes(etree.tostring(xml, pretty_print=True))
    print("zapsano", out)
    device.close()
PY
```

Ověř, že oba soubory obsahují alespoň jeden `<ipv6-nd-entry>` a alespoň jednu `fe80::` adresu:

```bash
grep -c "ipv6-nd-entry" tests/fixtures/rpc/junos/nd.xml tests/fixtures/rpc/junos-evo/nd.xml
grep -c "fe80::" tests/fixtures/rpc/junos/nd.xml tests/fixtures/rpc/junos-evo/nd.xml
```

- [ ] **Step 2: Napiš padající test**

Vytvoř `tests/collectors/test_nd.py`:

```python
import pytest

from migration_validator.collectors.nd import NdCollector

PLATFORMS = ("junos", "junos-evo")


@pytest.mark.parametrize("platform", PLATFORMS)
def test_returns_list_of_entries(rpc_fixture, platform):
    result = NdCollector().parse(rpc_fixture(platform, "nd"), platform)
    assert isinstance(result, list)
    assert result, "fixture nema zadny ND zaznam"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_entries_have_expected_keys(rpc_fixture, platform):
    result = NdCollector().parse(rpc_fixture(platform, "nd"), platform)
    for entry in result:
        assert set(entry) == {"ip", "mac", "interface", "state"}
        assert entry["ip"]
        assert entry["interface"]


@pytest.mark.parametrize("platform", PLATFORMS)
def test_values_are_stripped(rpc_fixture, platform):
    """MX obaluje hodnoty novymi radky, EVO ne - ping by dostal nevalidni cil."""
    result = NdCollector().parse(rpc_fixture(platform, "nd"), platform)
    for entry in result:
        for key in ("ip", "mac", "interface", "state"):
            value = entry[key]
            if value is not None:
                assert value == value.strip(), f"{key} nese bile znaky: {value!r}"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_link_local_entries_are_kept_by_collector(rpc_fixture, platform):
    """Collector neinterpretuje - filtr link-local patri do resolvovani cilu."""
    result = NdCollector().parse(rpc_fixture(platform, "nd"), platform)
    assert any(entry["ip"].lower().startswith("fe80:") for entry in result)


def test_collector_metadata():
    collector = NdCollector()
    assert collector.name == "nd"
    assert collector.rpc_name("junos") == "get_ipv6_nd_information"
    assert collector.rpc_name("junos-evo") == "get_ipv6_nd_information"
```

- [ ] **Step 3: Spusť test a ověř, že padá**

Run: `.venv/bin/pytest tests/collectors/test_nd.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'migration_validator.collectors.nd'`

- [ ] **Step 4: Napiš collector**

Vytvoř `migration_validator/collectors/nd.py`:

```python
"""Sber ND tabulky (IPv6 sousede).

Dvojce arp.py - z ND se odvozuji cile pingu pro IPv6, stejne jako se z ARP
odvozuji pro IPv4.

Collector zaznamy nefiltruje. Link-local sousede i zaznamy bez MAC se vraci
vsechny; rozhodnuti, co je pouzitelny cil pingu, patri do probes/ping.py,
protoze zavisi na konfiguraci sluzby, kterou collector nezna.

Overeno proti laborce: RPC i jmena prvku jsou shodna na vMX i na EVO. MX
obaluje texty novymi radky, EVO ne - _text() to resi strippingem.
"""

from __future__ import annotations

from typing import Any

from lxml import etree

from migration_validator.collectors.base import Collector
from migration_validator.collectors.interfaces import _text
from migration_validator.collectors.registry import register


@register
class NdCollector(Collector):
    name = "nd"

    def rpc_name(self, platform: str) -> str:
        return "get_ipv6_nd_information"

    def parse(self, xml: etree._Element, platform: str) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []

        for node in xml.iter("ipv6-nd-entry"):
            address = _text(node, "ipv6-nd-neighbor-address")
            interface = _text(node, "ipv6-nd-interface-name")
            if not address or not interface:
                continue
            entries.append(
                {
                    "ip": address,
                    "mac": _text(node, "ipv6-nd-neighbor-l2-address"),
                    "interface": interface,
                    "state": _text(node, "ipv6-nd-state"),
                }
            )

        return entries
```

- [ ] **Step 5: Zaregistruj collector**

V `migration_validator/collectors/all.py` doplň import:

```python
from migration_validator.collectors import arp, bgp, evpn, interfaces, nd  # noqa: F401
```

- [ ] **Step 6: Zapiš obor na všechna tři místa**

V `migration_validator/models/scope.py`:

```python
FACT_AREAS = ("interfaces", "arp", "nd", "bgp", "evpn_vpws", "evpn_esi", "evpn_mac")
```

```python
def _empty(area: str) -> Any:
    return [] if area in ("arp", "nd") else {}
```

V `migration_validator/capture.py`:

```python
LIST_AREAS = frozenset({"arp", "nd"})
```

- [ ] **Step 7: Přidej filtrovací větev do `Scope.select`**

V `migration_validator/models/scope.py` v metodě `Scope.select` doplň za blok `arp`:

```python
        nd = [
            entry
            for entry in (facts.get("nd") or [])
            if self.selectors.matches_interface(str(entry.get("interface", "")))
        ]
```

a do návratového slovníku:

```python
            "nd": nd,
```

- [ ] **Step 8: Napiš test na správný prázdný typ**

Přidej do `tests/models/test_scope.py`:

```python
from migration_validator.models.scope import FACT_AREAS, device_scope


def test_nd_area_is_a_list_when_missing():
    """Spatny prazdny typ by check videl jako prazdny slovnik a tise prosel."""
    selected = device_scope().select({})

    assert selected["nd"] == []
    assert "nd" in FACT_AREAS
```

- [ ] **Step 9: Doplň ND do conformance testu a conftestu**

V `tests/collectors/test_conformance.py` přidej import a položku do `COLLECTORS`:

```python
from migration_validator.collectors.nd import NdCollector
```

```python
COLLECTORS = (
    InterfacesCollector(),
    ArpCollector(),
    NdCollector(),
    BgpCollector(),
    EvpnVpwsCollector(),
    EvpnEsiCollector(),
    EvpnMacCollector(),
)
```

V `tests/conftest.py` doplň do `_facts_for` prázdný obor a do návratu:

```python
    nd = []
```

```python
        "nd": nd,
```

a do seznamu collectorů v `CaptureMeta`:

```python
                collectors={
                    name: {"status": "ok"}
                    for name in (
                        "interfaces", "arp", "nd", "bgp",
                        "evpn_vpws", "evpn_esi", "evpn_mac",
                    )
                },
```

- [ ] **Step 10: Spusť testy a ověř, že prochází**

Run: `.venv/bin/pytest tests/collectors tests/models/test_scope.py -v`
Expected: PASS

- [ ] **Step 11: Commit**

```bash
git add migration_validator/collectors/nd.py migration_validator/collectors/all.py migration_validator/models/scope.py migration_validator/capture.py tests/
git commit -m "feat(collectors): add ND collector for IPv6 neighbours

RPC name and element names verified against both vMX 24.2 and PTX EVO
25.2 - they are identical, so no platform branch is needed.

The collector keeps link-local and incomplete entries; deciding which of
them is a usable ping target depends on service configuration the
collector cannot see."
```

---

## Task 7: Checky ARP a ND — rodiny, MAC a hodnoty

**Files:**
- Modify: `migration_validator/checks/reachability.py`
- Modify: `tests/checks/test_reachability.py`

**Interfaces:**
- Consumes: obor `nd` z Tasku 6, pole `family` / `value` z Tasku 5
- Produces: check `nd_present` (`id = "nd_present"`, `requires = ("nd",)`, `mode = Mode.STATE`, `default_severity = Severity.ADVISORY`, `service_types = CUSTOMER_SERVICE_TYPES`). `arp_present` a `nd_present` vracejí **jeden `Finding` na záznam** s `label` = `"ARP"` resp. `"ND"`, `family` = `4` resp. `6` a `value` ve tvaru `"<mac> -> <ip>"`.

- [ ] **Step 1: Napiš padající testy**

Přidej do `tests/checks/test_reachability.py`:

```python
from migration_validator.checks.base import CheckContext
from migration_validator.checks.reachability import ArpPresentCheck, NdPresentCheck
from migration_validator.config import default_config
from migration_validator.models.result import Outcome
from migration_validator.models.scope import Scope, ScopeKey, Selectors


def _service_scope() -> Scope:
    return Scope(
        id="svc:INTERNET-CPE13-NNI:Internet",
        kind="service",
        key=ScopeKey(description="INTERNET-CPE13-NNI", service_type="Internet"),
        selectors=Selectors(
            interfaces=["et-0/0/8.13"],
            local_ipv4=["152.11.13.1/30"],
            local_ipv6=["2001:abcd:11:13::a/127"],
        ),
    )


def _ctx(subject: dict) -> CheckContext:
    return CheckContext(
        scope=_service_scope(),
        subject=subject,
        baseline=None,
        config=default_config(),
    )


def test_arp_finding_shows_mac_and_family():
    ctx = _ctx(
        {
            "arp": [
                {
                    "ip": "152.11.13.2",
                    "mac": "0c:00:ef:5e:df:01",
                    "interface": "et-0/0/8.13",
                    "routing_instance": None,
                }
            ]
        }
    )

    findings = ArpPresentCheck().run(ctx)

    assert len(findings) == 1
    assert findings[0].family == 4
    assert findings[0].label == "ARP"
    assert findings[0].value == "0c:00:ef:5e:df:01 -> 152.11.13.2"
    assert findings[0].outcome is Outcome.OK


def test_nd_finding_shows_mac_and_family():
    ctx = _ctx(
        {
            "nd": [
                {
                    "ip": "2001:abcd:11:13::b",
                    "mac": "0c:00:ef:5e:df:01",
                    "interface": "et-0/0/8.13",
                    "state": "reachable",
                }
            ]
        }
    )

    findings = NdPresentCheck().run(ctx)

    assert len(findings) == 1
    assert findings[0].family == 6
    assert findings[0].label == "ND"
    assert findings[0].value == "0c:00:ef:5e:df:01 -> 2001:abcd:11:13::b"


def test_nd_ignores_link_local_when_not_configured():
    ctx = _ctx(
        {
            "nd": [
                {
                    "ip": "fe80::c66b:b8ff:fe48:0",
                    "mac": "c4:6b:b8:48:00:00",
                    "interface": "et-0/0/8.13",
                    "state": "stale",
                }
            ]
        }
    )

    findings = NdPresentCheck().run(ctx)

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.BROKEN


def test_nd_keeps_link_local_when_configured():
    scope = _service_scope()
    scope.selectors.local_ipv6 = ["fe80::1/64"]
    ctx = CheckContext(
        scope=scope,
        subject={
            "nd": [
                {
                    "ip": "fe80::c66b:b8ff:fe48:0",
                    "mac": "c4:6b:b8:48:00:00",
                    "interface": "et-0/0/8.13",
                    "state": "stale",
                }
            ]
        },
        baseline=None,
        config=default_config(),
    )

    findings = NdPresentCheck().run(ctx)

    assert findings[0].outcome is Outcome.OK
    assert findings[0].value == "c4:6b:b8:48:00:00 -> fe80::c66b:b8ff:fe48:0"


def test_arp_without_entries_is_broken():
    findings = ArpPresentCheck().run(_ctx({"arp": []}))

    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].family == 4


def test_finding_records_which_configured_range_it_belongs_to():
    """Report tim popisuje radky u sluzby s vic rozsahy jedne rodiny."""
    ctx = _ctx(
        {
            "arp": [
                {
                    "ip": "152.11.13.2",
                    "mac": "0c:00:ef:5e:df:01",
                    "interface": "et-0/0/8.13",
                    "routing_instance": None,
                }
            ]
        }
    )

    findings = ArpPresentCheck().run(ctx)

    assert findings[0].details["address"] == "152.11.13.1/30"


def test_owning_prefix_picks_the_containing_range():
    from migration_validator.checks.reachability import owning_prefix

    prefixes = ["152.11.13.1/30", "152.11.20.1/29"]

    assert owning_prefix("152.11.20.2", prefixes) == "152.11.20.1/29"
    assert owning_prefix("10.9.9.9", prefixes) is None
    assert owning_prefix("2001:db8::1", prefixes) is None
```

- [ ] **Step 2: Spusť testy a ověř, že padají**

Run: `.venv/bin/pytest tests/checks/test_reachability.py -v`
Expected: FAIL — `ImportError: cannot import name 'NdPresentCheck'`

- [ ] **Step 3: Přidej sdílenou funkci pro link-local**

Do `migration_validator/checks/reachability.py` přidej nahoru za importy:

```python
import ipaddress

from migration_validator.models.scope import Scope


def link_local_is_configured(scope: Scope) -> bool:
    """Ma sluzba link-local adresu primo pod rozhranim?

    Link-local sousede se objevi u kazdeho IPv6 rozhrani a o zakaznicke
    sluzbe nerikaji nic. Existuji ale nasazeni, kde je link-local jedina
    nakonfigurovana adresa - pak je to legitimni soused. Rozhoduje
    konfigurace, ne heuristika.
    """
    for address in scope.selectors.local_ipv6:
        try:
            if ipaddress.ip_interface(address).ip.is_link_local:
                return True
        except ValueError:
            continue
    return False


def is_link_local(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_link_local
    except ValueError:
        return False


def owning_prefix(address: str, prefixes: list[str]) -> str | None:
    """Ktery nakonfigurovany rozsah tuhle adresu obsahuje.

    Report tim popisuje radky u sluzby, ktera ma vic rozsahu jedne rodiny -
    bez toho by u dvou ARP zaznamu nebylo poznat, ke kteremu rozsahu patri.
    U jedineho rozsahu se to v reportu zahodi, protoze uz je v hlavicce.
    """
    try:
        target = ipaddress.ip_address(address)
    except ValueError:
        return None

    for prefix in prefixes:
        try:
            network = ipaddress.ip_interface(prefix).network
        except ValueError:
            continue
        if target.version == network.version and target in network:
            return prefix
    return None
```

- [ ] **Step 4: Přepiš `ArpPresentCheck` na jeden finding na záznam**

Nahraď tělo `ArpPresentCheck.run`:

```python
    def run(self, ctx: CheckContext) -> list[Finding]:
        entries: list[dict[str, Any]] = ctx.subject.get("arp", [])
        entries = [entry for entry in entries if entry.get("ip")]

        if not entries:
            return [
                Finding(
                    Outcome.BROKEN,
                    "na rozhranich sluzby neni zadny ARP zaznam",
                    label="ARP",
                    family=4,
                    value="zadny zaznam",
                    subject={"count": 0, "addresses": []},
                )
            ]

        prefixes = ctx.scope.selectors.local_ipv4
        return [
            Finding(
                Outcome.OK,
                f"ARP zaznam {entry['ip']}",
                label="ARP",
                family=4,
                value=f"{entry.get('mac') or '?'} -> {entry['ip']}",
                subject={"ip": entry["ip"], "mac": entry.get("mac")},
                details={"address": owning_prefix(str(entry["ip"]), prefixes)},
            )
            for entry in entries
        ]
```

- [ ] **Step 5: Přidej `NdPresentCheck`**

Za `ArpPresentCheck` přidej:

```python
@register
class NdPresentCheck(Check):
    id = "nd_present"
    title = "Existence ND zaznamu"
    mode = Mode.STATE
    requires = ("nd",)
    requires_inventory = True
    service_types = CUSTOMER_SERVICE_TYPES
    default_severity = Severity.ADVISORY

    def run(self, ctx: CheckContext) -> list[Finding]:
        keep_link_local = link_local_is_configured(ctx.scope)
        entries = [
            entry
            for entry in ctx.subject.get("nd", [])
            if entry.get("ip")
            and (keep_link_local or not is_link_local(str(entry["ip"])))
        ]

        if not entries:
            return [
                Finding(
                    Outcome.BROKEN,
                    "na rozhranich sluzby neni zadny pouzitelny ND zaznam",
                    label="ND",
                    family=6,
                    value="zadny zaznam",
                    subject={"count": 0, "addresses": []},
                )
            ]

        prefixes = ctx.scope.selectors.local_ipv6
        return [
            Finding(
                Outcome.OK,
                f"ND zaznam {entry['ip']}",
                label="ND",
                family=6,
                value=f"{entry.get('mac') or '?'} -> {entry['ip']}",
                subject={
                    "ip": entry["ip"],
                    "mac": entry.get("mac"),
                    "state": entry.get("state"),
                },
                details={"address": owning_prefix(str(entry["ip"]), prefixes)},
            )
            for entry in entries
        ]
```

- [ ] **Step 6: Doplň rodinu do `PingReachabilityCheck`**

V `PingReachabilityCheck.run` se probe seskupí podle rodiny a vrátí se jeden finding na rodinu. Nahraď celé tělo metody:

```python
    def run(self, ctx: CheckContext) -> list[Finding]:
        probes: list[dict[str, Any]] = ctx.subject.get("ping", [])
        if not probes:
            return [
                Finding(
                    Outcome.SKIP,
                    "pro tento scope nejsou ve snapshotu zadne cile pingu",
                    label="Ping",
                )
            ]

        findings: list[Finding] = []
        for family in (4, 6):
            batch = [probe for probe in probes if probe.get("family") == family]
            if not batch:
                continue
            prefixes = (
                ctx.scope.selectors.local_ipv6
                if family == 6
                else ctx.scope.selectors.local_ipv4
            )
            findings.extend(_ping_findings(batch, family, prefixes))
        return findings
```

A na konec souboru přidej pomocnou funkci:

```python
def _ping_findings(
    probes: list[dict[str, Any]], family: int, prefixes: list[str]
) -> list[Finding]:
    """Jeden radek na cil - report je vypisuje jednotlive, ne jako souhrn."""
    findings = []
    for probe in probes:
        target = str(probe.get("target"))
        sent = int(probe.get("sent", 0))
        received = int(probe.get("received", 0))
        rtt = probe.get("rtt_avg_ms")
        value = f"{received}/{sent}"
        if rtt is not None:
            value = f"{value}  {rtt} ms"

        details = {
            "resolved_from": probe.get("resolved_from"),
            "address": owning_prefix(target, prefixes),
        }

        if received:
            findings.append(
                Finding(
                    Outcome.OK,
                    f"{target}: odpovedelo {received} z {sent}",
                    label="Ping",
                    family=family,
                    value=value,
                    subject={"target": target, "sent": sent, "received": received},
                    details=details,
                )
            )
        else:
            findings.append(
                Finding(
                    Outcome.BROKEN,
                    f"{target}: neodpovedel ({sent} paketu)",
                    label="Ping",
                    family=family,
                    value=f"{value}  {target} neodpovedel",
                    subject={"target": target, "sent": sent, "received": 0},
                    details=details,
                )
            )
    return findings
```

- [ ] **Step 7: Spusť testy a ověř, že prochází**

Run: `.venv/bin/pytest tests/checks/test_reachability.py -v`
Expected: PASS

- [ ] **Step 8: Spusť celou sadu a oprav následky**

Run: `.venv/bin/pytest -q`
Expected: mohou padat testy, které předpokládaly jeden souhrnný ARP/ping finding. Uprav je na nový tvar (finding na záznam), neměň chování zpět.

- [ ] **Step 9: Commit**

```bash
git add migration_validator/checks/reachability.py tests/checks/test_reachability.py
git commit -m "feat(checks): add nd_present, one finding per neighbour, MAC in value

Link-local neighbours are dropped unless the service actually has a
link-local address configured under the interface - configuration
decides, not a heuristic."
```

---

## Task 8: Ping — rapid, IPv6 cíle, zdroj podle rodiny

**Files:**
- Modify: `migration_validator/probes/ping.py`
- Modify: `migration_validator/capture.py` (předání ND do `resolve_targets`)
- Modify: `tests/probes/test_ping.py`

**Interfaces:**
- Consumes: obor `nd` z Tasku 6, `Selectors` z Tasku 4
- Produces: `PingTarget(scope_id, target, source, routing_instance, resolved_from, family, interface=None)` — `family` je `int`, `interface` je `str | None` (vyplněné jen u link-local cíle). `resolve_targets(scopes, arp_entries, nd_entries) -> list[PingTarget]` (třetí parametr je nový). `to_dict()` `PingTarget`u nese `family` i `interface`.

- [ ] **Step 1: Napiš padající testy**

Přidej do `tests/probes/test_ping.py`:

```python
from migration_validator.models.scope import Scope, ScopeKey, Selectors
from migration_validator.probes.ping import (
    PingTarget,
    resolve_targets,
    source_address,
    subnet_fallback,
)


def _scope(**selector_kwargs) -> Scope:
    return Scope(
        id="svc:X:Internet",
        kind="service",
        key=ScopeKey(description="X", service_type="Internet"),
        selectors=Selectors(interfaces=["et-0/0/8.13"], **selector_kwargs),
    )


def test_source_follows_target_family():
    scope = _scope(
        local_ipv4=["152.11.13.1/30"],
        local_ipv6=["2001:abcd:11:13::a/127"],
    )

    assert source_address(scope, 4) == "152.11.13.1"
    assert source_address(scope, 6) == "2001:abcd:11:13::a"


def test_virtual_gateway_wins_over_interface_address():
    scope = _scope(
        local_ipv4=["152.11.14.2/29"],
        virtual_gw_v4=["152.11.14.1"],
    )

    assert source_address(scope, 4) == "152.11.14.1"


def test_ipv6_targets_come_from_nd():
    scope = _scope(local_ipv6=["2001:abcd:11:13::a/127"])
    nd = [
        {
            "ip": "2001:abcd:11:13::b",
            "mac": "0c:00:ef:5e:df:01",
            "interface": "et-0/0/8.13",
            "state": "reachable",
        }
    ]

    targets = resolve_targets([scope], [], nd)

    assert [t.target for t in targets] == ["2001:abcd:11:13::b"]
    assert targets[0].family == 6
    assert targets[0].resolved_from == "nd"


def test_incomplete_nd_entry_is_not_a_target():
    """Zaznam bez MAC neni cil - strilet na nej nema smysl."""
    scope = _scope(local_ipv6=["2001:db8::2/64"])
    nd = [
        {
            "ip": "2001:db8::1",
            "mac": "none",
            "interface": "et-0/0/8.13",
            "state": "unreachable",
        }
    ]

    assert resolve_targets([scope], [], nd) == []


def test_link_local_target_carries_interface():
    scope = _scope(local_ipv6=["fe80::1/64"])
    nd = [
        {
            "ip": "fe80::c66b:b8ff:fe48:0",
            "mac": "c4:6b:b8:48:00:00",
            "interface": "et-0/0/8.13",
            "state": "stale",
        }
    ]

    targets = resolve_targets([scope], [], nd)

    assert targets[0].interface == "et-0/0/8.13"


def test_link_local_ignored_when_not_configured():
    scope = _scope(local_ipv6=["2001:abcd:11:13::a/127"])
    nd = [
        {
            "ip": "fe80::c66b:b8ff:fe48:0",
            "mac": "c4:6b:b8:48:00:00",
            "interface": "et-0/0/8.13",
            "state": "stale",
        }
    ]

    assert resolve_targets([scope], [], nd) == []


def test_ipv6_fallback_only_on_point_to_point():
    """Do /64 se nestrili - zarucene neuspesny ping se cte jako nedostupne CPE."""
    assert subnet_fallback(["2001:abcd:11:13::a/127"], 6) == "2001:abcd:11:13::b"
    assert subnet_fallback(["2001:db8::2/64"], 6) is None


def test_targets_are_ordered_ipv4_before_ipv6():
    scope = _scope(
        local_ipv4=["152.11.13.1/30"],
        local_ipv6=["2001:abcd:11:13::a/127"],
    )
    arp = [{"ip": "152.11.13.2", "interface": "et-0/0/8.13"}]
    nd = [
        {
            "ip": "2001:abcd:11:13::b",
            "mac": "0c:00:ef:5e:df:01",
            "interface": "et-0/0/8.13",
            "state": "reachable",
        }
    ]

    targets = resolve_targets([scope], arp, nd)

    assert [t.family for t in targets] == [4, 6]
```

- [ ] **Step 2: Spusť testy a ověř, že padají**

Run: `.venv/bin/pytest tests/probes/test_ping.py -v`
Expected: FAIL — `TypeError: source_address() takes 1 positional argument but 2 were given`

- [ ] **Step 3: Přepiš `PingTarget`**

V `migration_validator/probes/ping.py`:

```python
@dataclass(frozen=True)
class PingTarget:
    scope_id: str
    target: str
    source: str | None
    routing_instance: str | None
    resolved_from: str  # arp | nd | subnet-fallback
    family: int
    interface: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope_id": self.scope_id,
            "target": self.target,
            "source": self.source,
            "routing_instance": self.routing_instance,
            "resolved_from": self.resolved_from,
            "family": self.family,
            "interface": self.interface,
        }
```

- [ ] **Step 4: Přepiš `source_address` a `subnet_fallback`**

```python
def source_address(scope: Scope, family: int) -> str | None:
    """Adresa rozhrani v dane rodine. U IRB se pouziva virtual-gw.

    Rodina zdroje musi odpovidat rodine cile - jinak Junos ping odmitne.
    """
    if family == 6:
        gateway, local = scope.selectors.virtual_gw_v6, scope.selectors.local_ipv6
    else:
        gateway, local = scope.selectors.virtual_gw_v4, scope.selectors.local_ipv4

    if gateway:
        return gateway[0].split("/")[0]
    if local:
        return local[0].split("/")[0]
    return None


# Kratsi prefix nez tohle uz neni point-to-point linka. V IPv6 nema smysl
# strilet nahodnou adresu ze /64 - je to zaruceny neuspech, ktery se v
# reportu cte jako nedostupne CPE.
IPV6_FALLBACK_MIN_PREFIX = 126


def subnet_fallback(addresses: list[str], family: int) -> str | None:
    """Prvni pouzitelna adresa ze subnetu, ktera neni nase vlastni."""
    for address in addresses:
        try:
            interface = ipaddress.ip_interface(address)
        except ValueError:
            continue

        network = interface.network
        if network.prefixlen >= network.max_prefixlen:
            continue
        if family == 6 and network.prefixlen < IPV6_FALLBACK_MIN_PREFIX:
            continue

        for candidate in network:
            if candidate == interface.ip:
                continue
            # Vyloucit sit a broadcast je IPv4 uvaha - v IPv6 je adresa se
            # samymi nulami subnet-router anycast, ne broadcast.
            if family == 4 and network.prefixlen < network.max_prefixlen - 1:
                if candidate in (network.network_address, network.broadcast_address):
                    continue
            return str(candidate)
    return None
```

- [ ] **Step 5: Přepiš `resolve_targets`**

```python
def _is_link_local(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_link_local
    except ValueError:
        return False


def _link_local_configured(scope: Scope) -> bool:
    for address in scope.selectors.local_ipv6:
        try:
            if ipaddress.ip_interface(address).ip.is_link_local:
                return True
        except ValueError:
            continue
    return False


def _usable_nd(entry: dict[str, Any]) -> bool:
    """Zaznam bez MAC nebo v nedokoncenem stavu neni cil."""
    mac = (entry.get("mac") or "").strip().lower()
    state = (entry.get("state") or "").strip().lower()
    return bool(mac) and mac != "none" and state not in ("unreachable", "incomplete")


def resolve_targets(
    scopes: list[Scope],
    arp_entries: list[dict[str, Any]],
    nd_entries: list[dict[str, Any]] | None = None,
) -> list[PingTarget]:
    """Odvodi cile pingu ze scopu, ARP tabulky (IPv4) a ND tabulky (IPv6).

    Cile se vraci setridene IPv4 pred IPv6, aby report nemusel rodinu hadat
    zpetne z textu adresy.
    """
    nd_entries = nd_entries or []
    targets: list[PingTarget] = []

    for scope in scopes:
        if scope.is_device or scope.service_type not in PING_SERVICE_TYPES:
            continue

        instance = (
            scope.selectors.routing_instances[0]
            if scope.service_type == "IPVPN" and scope.selectors.routing_instances
            else None
        )
        keep_link_local = _link_local_configured(scope)

        for family in (4, 6):
            source = source_address(scope, family)

            if family == 4:
                addresses = [
                    (str(entry["ip"]), None)
                    for entry in arp_entries
                    if scope.selectors.matches_interface(str(entry.get("interface", "")))
                    and entry.get("ip")
                ]
                origin = "arp"
                local = scope.selectors.local_ipv4
            else:
                addresses = [
                    (
                        str(entry["ip"]),
                        # Link-local cil bez interface Junos odmitne (overeno).
                        str(entry["interface"]) if _is_link_local(str(entry["ip"])) else None,
                    )
                    for entry in nd_entries
                    if scope.selectors.matches_interface(str(entry.get("interface", "")))
                    and entry.get("ip")
                    and _usable_nd(entry)
                    and (keep_link_local or not _is_link_local(str(entry["ip"])))
                ]
                origin = "nd"
                local = scope.selectors.local_ipv6

            if addresses:
                targets.extend(
                    PingTarget(scope.id, address, source, instance, origin, family, interface)
                    for address, interface in addresses
                )
                continue

            fallback = subnet_fallback(local, family)
            if fallback:
                targets.append(
                    PingTarget(
                        scope.id, fallback, source, instance, "subnet-fallback", family
                    )
                )

    return targets
```

- [ ] **Step 6: Přidej `rapid` a `interface` do `run_ping`**

V `run_ping` nahraď sestavení `kwargs`:

```python
    kwargs: dict[str, Any] = {
        "host": target.target,
        "count": str(count),
        # Bez rapid trva 5 paketu ~5 s, s nim ~0,3 s. Overeno proti laborce,
        # ze tvar odpovedi zustava stejny - probe-results-summary se stejnymi
        # poli - takze parse_ping_result se nemeni.
        "rapid": True,
    }
    if target.source:
        kwargs["source"] = target.source
    if target.routing_instance:
        kwargs["routing_instance"] = target.routing_instance
    if target.interface:
        kwargs["interface"] = target.interface
```

- [ ] **Step 7: Předej ND do `resolve_targets` v capture**

V `migration_validator/capture.py` nahraď:

```python
        for target in resolve_targets(scopes, facts.get("arp", [])):
```

za:

```python
        for target in resolve_targets(scopes, facts.get("arp", []), facts.get("nd", [])):
```

- [ ] **Step 8: Spusť testy a ověř, že prochází**

Run: `.venv/bin/pytest tests/probes/test_ping.py -v`
Expected: PASS

- [ ] **Step 9: Spusť celou sadu**

Run: `.venv/bin/pytest -q`
Expected: PASS. Testy, které volaly `resolve_targets` se dvěma argumenty, projdou díky výchozí hodnotě třetího.

- [ ] **Step 10: Commit**

```bash
git add migration_validator/probes/ping.py migration_validator/capture.py tests/probes/test_ping.py
git commit -m "feat(ping): rapid mode, IPv6 targets from ND, source per family

Targets carry their family instead of the report guessing it back out of
the address text - that is the actual fix for results arriving
interleaved.

IPv6 subnet fallback is limited to /126 and longer: firing at an
arbitrary address inside a /64 is a guaranteed miss that reads as an
unreachable CPE."
```

---

## Task 9: BGP po RIB

**Files:**
- Modify: `migration_validator/collectors/bgp.py`
- Modify: `migration_validator/checks/bgp.py`
- Modify: `tests/collectors/test_bgp.py`, `tests/checks/test_bgp.py`, `tests/conftest.py`

**Interfaces:**
- Consumes: `family` / `value` z Tasku 5
- Produces: `BgpCollector.parse()` vrací pro každého peera `{"state", "peer_as", "routing_instance", "ribs": {<jmeno_rib>: {"received", "accepted", "advertised", "active", "suppressed"}}}`. Klíč `prefixes` zaniká. `bgp_session_state` a `bgp_prefix_counts` vracejí findingy s `family` odvozenou z adresy peeru.

- [ ] **Step 1: Napiš padající testy**

Přidej do `tests/collectors/test_bgp.py`:

```python
import pytest

from migration_validator.collectors.bgp import BgpCollector

PLATFORMS = ("junos", "junos-evo")

RIB_KEYS = {"received", "accepted", "advertised", "active", "suppressed"}


@pytest.mark.parametrize("platform", PLATFORMS)
def test_ribs_are_kept_apart(rpc_fixture, platform):
    peers = BgpCollector().parse(rpc_fixture(platform, "bgp"), platform)
    assert peers, "fixture nema zadneho peera"

    for peer, data in peers.items():
        assert "prefixes" not in data, f"{peer}: souctove pole prezilo"
        assert data["ribs"], f"{peer}: zadna RIB"
        for rib_name, counts in data["ribs"].items():
            assert rib_name
            assert set(counts) == RIB_KEYS


@pytest.mark.parametrize("platform", PLATFORMS)
def test_known_rib_name_is_present(rpc_fixture, platform):
    peers = BgpCollector().parse(rpc_fixture(platform, "bgp"), platform)
    names = {rib for data in peers.values() for rib in data["ribs"]}

    assert "inet.0" in names
```

Přidej do `tests/checks/test_bgp.py`:

```python
from migration_validator.checks.base import CheckContext
from migration_validator.checks.bgp import BgpSessionStateCheck
from migration_validator.config import default_config
from migration_validator.models.scope import Scope, ScopeKey, Selectors


def _ctx(subject, baseline=None) -> CheckContext:
    return CheckContext(
        scope=Scope(
            id="svc:X:Internet",
            kind="service",
            key=ScopeKey(description="X", service_type="Internet"),
            selectors=Selectors(
                interfaces=["et-0/0/8.13"],
                bgp_neighbors=["152.11.13.2", "2001:abcd:11:13::b"],
            ),
        ),
        subject=subject,
        baseline=baseline,
        config=default_config(),
    )


def test_session_findings_carry_peer_family():
    subject = {
        "bgp": {
            "152.11.13.2": {
                "state": "Established",
                "peer_as": 100,
                "routing_instance": None,
                "ribs": {"inet.0": {"received": 2, "accepted": 2, "advertised": 1,
                                    "active": 2, "suppressed": 0}},
            },
            "2001:abcd:11:13::b": {
                "state": "Established",
                "peer_as": 100,
                "routing_instance": None,
                "ribs": {"inet6.0": {"received": 0, "accepted": 0, "advertised": 1,
                                     "active": 0, "suppressed": 0}},
            },
        }
    }

    findings = BgpSessionStateCheck().run(_ctx(subject))
    by_family = {finding.family for finding in findings}

    assert by_family == {4, 6}
    assert all(finding.label == "BGP status" for finding in findings)
    assert all(finding.value == "Established" for finding in findings)
```

- [ ] **Step 2: Spusť testy a ověř, že padají**

Run: `.venv/bin/pytest tests/collectors/test_bgp.py tests/checks/test_bgp.py -v`
Expected: FAIL — `AssertionError: ... souctove pole prezilo` a `KeyError: 'ribs'`

- [ ] **Step 3: Přepiš collector na per-RIB**

V `migration_validator/collectors/bgp.py` nahraď v `parse` blok počítající `prefixes`:

```python
            ribs: dict[str, dict[str, int]] = {}
            for rib in node.iter("bgp-rib"):
                rib_name = _text(rib, "name")
                if not rib_name:
                    continue
                ribs[rib_name] = {
                    "received": _int(rib, "received-prefix-count"),
                    "accepted": _int(rib, "accepted-prefix-count"),
                    "advertised": _int(rib, "advertised-prefix-count"),
                    "active": _int(rib, "active-prefix-count"),
                    "suppressed": _int(rib, "suppressed-prefix-count"),
                }
```

a v konstrukci `peers[address]` nahraď `"prefixes": prefixes,` za `"ribs": ribs,`.

Uprav docstring modulu — nahraď větu o sčítání:

```
Overeno proti laborce: peer-address nese port ('150.0.0.1+179' na MX,
efemerni port '150.0.0.1+57010' na EVO) a jeden peer muze mit az 11 RIB
(bgp.rtarget.0, inet.0, bgp.l3vpn.0, ...). Countery se ukladaji za kazdou
RIB zvlast - souctem by se IPv4 a IPv6 slily do jednoho cisla a pokles v
inet6.0 kompenzovany narustem v inet.0 by prosel bez povsimnuti.
```

- [ ] **Step 4: Přidej rodinu do BGP checků**

Do `migration_validator/checks/bgp.py` přidej nahoru:

```python
import ipaddress


def peer_family(peer: str) -> int | None:
    """Rodina se odvozuje z adresy peeru, ne ze jmena RIB.

    Jmeno RIB rodinu nemusi obsahovat vubec (bgp.l3vpn.0). Znamé omezeni:
    peer s IPv4 adresou nesouci IPv6 RIB se cely zaradi do sekce IPv4.
    """
    try:
        return ipaddress.ip_address(peer).version
    except ValueError:
        return None
```

V `BgpSessionStateCheck.run` doplň do **každého** ze čtyř `Finding(...)` v cyklu:

```python
                        label="BGP status",
                        family=peer_family(peer),
                        value=state,
```

U větve s nezměněným stavem je `value=state` shodné s `baseline_value`; doplň tam navíc:

```python
                    baseline_value=baseline_state,
```

- [ ] **Step 5: Přepiš `BgpPrefixCountsCheck` na porovnání po RIB**

Nahraď v `BgpPrefixCountsCheck.run` tělo cyklu tak, aby procházelo RIB. Nahraď funkci `_counts` (na konci souboru) a cyklus:

```python
        findings = []
        for peer in sorted(peers):
            family = peer_family(peer)
            if peer not in baseline_peers:
                findings.append(
                    Finding(
                        Outcome.SKIP,
                        f"{peer}: peer neni v baseline snapshotu, nelze porovnat",
                        label="BGP prefixy",
                        family=family,
                    )
                )
                continue

            subject_ribs = peers[peer].get("ribs", {})
            baseline_ribs = baseline_peers[peer].get("ribs", {})

            for rib_name in sorted(subject_ribs):
                subject = subject_ribs[rib_name]
                baseline = baseline_ribs.get(rib_name)
                if baseline is None:
                    findings.append(
                        Finding(
                            Outcome.SKIP,
                            f"{peer}/{rib_name}: RIB neni v baseline, nelze porovnat",
                            label=f"BGP prefixy ({rib_name})",
                            family=family,
                        )
                    )
                    continue

                for key in PREFIX_KEYS:
                    findings.append(
                        _prefix_finding(
                            peer, rib_name, key, baseline[key], subject[key],
                            tolerance, family,
                        )
                    )
        return findings
```

Konstantu `PREFIX_KEYS` rozšiř:

```python
PREFIX_KEYS = ("active", "received", "accepted", "advertised", "suppressed")
```

A na konec souboru přidej:

```python
def _prefix_finding(
    peer: str,
    rib_name: str,
    key: str,
    baseline: int,
    subject: int,
    tolerance: float,
    family: int | None,
) -> Finding:
    """Jeden radek na counter - report je vypisuje jednotlive."""
    label = f"BGP {key}-prefix-count"
    change = percent_change(baseline, subject)
    delta = None
    if subject != baseline:
        delta = f"{subject - baseline:+d}"

    details = {"rib": rib_name, "tolerance_percent": tolerance}
    if change is not None:
        details["change_percent"] = round(change, 1)

    outcome = Outcome.OK
    message = f"{peer}/{rib_name}: {key} {subject}"
    if change is not None and change < tolerance:
        outcome = Outcome.BROKEN
        message = (
            f"{peer}/{rib_name}: pokles {key} {baseline} -> {subject}, "
            f"prah je {tolerance:.0f} %"
        )

    return Finding(
        outcome,
        message,
        label=label,
        family=family,
        value=str(subject),
        baseline_value=str(baseline),
        delta=delta,
        baseline={key: baseline},
        subject={key: subject},
        details=details,
    )
```

Odstraň nepoužitou funkci `_counts`, pokud po úpravě nemá volajícího.

- [ ] **Step 6: Sjednoť conftest**

V `tests/conftest.py` nahraď v `_facts_for`:

```python
                "prefixes": {"received": 14, "accepted": 14, "advertised": 3},
```

za:

```python
                "ribs": {
                    "inet.0": {
                        "received": 14,
                        "accepted": 14,
                        "advertised": 3,
                        "active": 14,
                        "suppressed": 0,
                    }
                },
```

- [ ] **Step 7: Spusť testy a ověř, že prochází**

Run: `.venv/bin/pytest tests/collectors/test_bgp.py tests/checks/test_bgp.py -v`
Expected: PASS

- [ ] **Step 8: Spusť celou sadu a oprav následky**

Run: `.venv/bin/pytest -q`
Expected: PASS po úpravě testů, které četly klíč `prefixes`.

- [ ] **Step 9: Commit**

```bash
git add migration_validator/collectors/bgp.py migration_validator/checks/bgp.py tests/
git commit -m "feat(bgp): keep prefix counters per RIB instead of summing them

Summing across RIBs merges the two families into one number, so BGP
cannot be split by family at all - and a drop in inet6.0 offset by a rise
in inet.0 used to pass unnoticed.

Adds active and suppressed counters, which were already in the RPC reply
but not collected."
```

---

## Task 10: Checky rozhraní — jemnější findingy a hodnoty

**Files:**
- Modify: `migration_validator/checks/ifaces.py`
- Modify: `tests/checks/test_ifaces.py`

**Interfaces:**
- Consumes: `family` / `value` / `baseline_value` / `delta` z Tasku 5
- Produces: `interface_state` vrací **dva findingy na rozhraní** — `label="Interface admin status"` a `label="Interface operational status"`, oba `family=None`. `interface_traffic` vrací dva findingy — `label="Interface traffic in"` a `label="Interface traffic out"` — s `value` ve tvaru `"460 pps"`, `baseline_value` a `delta` ve tvaru `"-12 %"`.

- [ ] **Step 1: Napiš padající testy**

Přidej do `tests/checks/test_ifaces.py`:

```python
from migration_validator.checks.base import CheckContext
from migration_validator.checks.ifaces import InterfaceStateCheck, InterfaceTrafficCheck
from migration_validator.config import default_config
from migration_validator.models.result import Outcome
from migration_validator.models.scope import Scope, ScopeKey, Selectors


def _ctx(subject, baseline=None) -> CheckContext:
    return CheckContext(
        scope=Scope(
            id="svc:X:Internet",
            kind="service",
            key=ScopeKey(description="X", service_type="Internet"),
            selectors=Selectors(interfaces=["et-0/0/8.13"]),
        ),
        subject=subject,
        baseline=baseline,
        config=default_config(),
    )


def _iface(pps: int) -> dict:
    return {
        "et-0/0/8.13": {
            "admin_status": "up",
            "oper_status": "up",
            "input_pps": pps,
            "output_pps": pps,
            "input_errors": 0,
            "output_errors": 0,
        }
    }


def test_state_check_splits_admin_and_oper():
    findings = InterfaceStateCheck().run(_ctx({"interfaces": _iface(400)}))
    labels = [finding.label for finding in findings]

    assert labels == ["Interface admin status", "Interface operational status"]
    assert all(finding.value == "Up" for finding in findings)
    assert all(finding.family is None for finding in findings)


def test_state_check_reports_the_broken_half():
    interfaces = _iface(400)
    interfaces["et-0/0/8.13"]["oper_status"] = "down"

    findings = InterfaceStateCheck().run(_ctx({"interfaces": interfaces}))

    assert findings[0].outcome is Outcome.OK
    assert findings[1].outcome is Outcome.BROKEN
    assert findings[1].value == "Down"


def test_traffic_check_reports_value_and_delta():
    findings = InterfaceTrafficCheck().run(
        _ctx({"interfaces": _iface(460)}, {"interfaces": _iface(520)})
    )
    by_label = {finding.label: finding for finding in findings}

    incoming = by_label["Interface traffic in"]
    assert incoming.value == "460 pps"
    assert incoming.baseline_value == "520 pps"
    assert incoming.delta == "-12 %"


def test_traffic_check_without_baseline_has_no_delta():
    findings = InterfaceTrafficCheck().run(_ctx({"interfaces": _iface(460)}))
    by_label = {finding.label: finding for finding in findings}

    assert by_label["Interface traffic in"].value == "460 pps"
    assert by_label["Interface traffic in"].baseline_value is None
    assert by_label["Interface traffic in"].delta is None
```

- [ ] **Step 2: Spusť testy a ověř, že padají**

Run: `.venv/bin/pytest tests/checks/test_ifaces.py -v`
Expected: FAIL — `AssertionError` na seznamu labelů (dnes vrací jeden finding s `label=name`)

- [ ] **Step 3: Rozděl `InterfaceStateCheck`**

Nahraď tělo `InterfaceStateCheck.run`:

```python
    def run(self, ctx: CheckContext) -> list[Finding]:
        interfaces: dict[str, Any] = ctx.subject.get("interfaces", {})
        if not interfaces:
            return [Finding(Outcome.SKIP, "pro tento scope nejsou data o rozhranich")]

        findings = []
        for name in sorted(interfaces):
            data = interfaces[name]
            for label, key in (
                ("Interface admin status", "admin_status"),
                ("Interface operational status", "oper_status"),
            ):
                state = str(data.get(key, "unknown"))
                ok = state == "up"
                findings.append(
                    Finding(
                        Outcome.OK if ok else Outcome.BROKEN,
                        f"{name}: {key} {state}",
                        label=label,
                        value=state.capitalize(),
                        subject={key: state},
                    )
                )
        return findings
```

- [ ] **Step 4: Rozděl `InterfaceTrafficCheck` na směry**

Nahraď `_state_finding` a `_compare_finding` jednou funkcí a uprav `run`. V `InterfaceTrafficCheck.run` nahraď tělo cyklu:

```python
        findings = []
        for name in names:
            subject = _rates(ctx.subject["interfaces"][name])
            baseline_data = (ctx.baseline or {}).get("interfaces", {}).get(name)
            baseline = _rates(baseline_data) if baseline_data else None

            for label, key in (
                ("Interface traffic in", "input_pps"),
                ("Interface traffic out", "output_pps"),
            ):
                findings.append(
                    _traffic_finding(
                        name, label, key, subject[key],
                        baseline[key] if baseline else None,
                        tolerance, require_nonzero,
                    )
                )
        return findings
```

A nahraď obě staré pomocné funkce jednou:

```python
def _traffic_finding(
    name: str,
    label: str,
    key: str,
    subject: int,
    baseline: int | None,
    tolerance: float,
    require_nonzero: bool,
) -> Finding:
    """Jeden smer provozu = jeden radek reportu.

    Bez baseline se hodnoti jen absolutni hodnota; delta zustava None a
    report ve sloupci ZMENA nevypise nic.
    """
    value = f"{subject} pps"

    if baseline is None:
        broken = require_nonzero and subject == 0
        return Finding(
            Outcome.BROKEN if broken else Outcome.OK,
            f"{name}: {key} {subject} pps",
            label=label,
            value=value,
            subject={key: subject},
        )

    change = percent_change(baseline, subject)
    delta = None if change is None else f"{change:+.0f} %"
    broken = change is not None and change < tolerance

    return Finding(
        Outcome.BROKEN if broken else Outcome.OK,
        (
            f"{name}: {key} kleslo o {abs(round(change))} % "
            f"({baseline} -> {subject}), prah je {tolerance:.0f} %"
            if broken
            else f"{name}: {key} v toleranci {tolerance:.0f} %"
        ),
        label=label,
        value=value,
        baseline_value=f"{baseline} pps",
        delta=delta,
        baseline={key: baseline},
        subject={key: subject},
        details={"tolerance_percent": tolerance},
    )
```

Ponech `_rates` beze změny. Odstraň `_state_finding` a `_compare_finding`, pokud po úpravě nemají volajícího — ověř grepem:

Run: `grep -rn "_state_finding\|_compare_finding" migration_validator tests`

- [ ] **Step 5: Spusť testy a ověř, že prochází**

Run: `.venv/bin/pytest tests/checks/test_ifaces.py -v`
Expected: PASS

- [ ] **Step 6: Spusť celou sadu a oprav následky**

Run: `.venv/bin/pytest -q`
Expected: PASS po úpravě testů, které předpokládaly jeden souhrnný finding na rozhraní.

- [ ] **Step 7: Commit**

```bash
git add migration_validator/checks/ifaces.py tests/checks/test_ifaces.py
git commit -m "feat(checks): split interface findings into one row per fact

The report shows label/value pairs, so admin and operational status are
separate rows, as are the two traffic directions - the split has to
happen in the check because only it knows what the value is."
```

---

## Task 11: `ServiceView` — data pro report

**Files:**
- Create: `migration_validator/reporting/view.py`
- Create: `tests/reporting/test_view.py`

**Interfaces:**
- Consumes: `ScopeResult.identity` a `CheckResult.family` / `.value` / `.baseline_value` / `.delta` z Tasku 5
- Produces:
  - `@dataclass Row(status: Status, label: str, value: str, baseline_value: str | None, delta: str | None, mode: str)`
  - `@dataclass Section(family: int | None, addresses: list[str], virtual_gw: list[str], rows: list[Row])`
  - `@dataclass ServiceView(status: Status, description: str, service_type: str, routing_instance: str | None, baseline_interfaces: list[str], subject_interfaces: list[str], worst_message: str, sections: list[Section])`
  - `build_view(scope: ScopeResult) -> ServiceView`
  - `change_text(row: Row, has_baseline: bool) -> str`

- [ ] **Step 1: Napiš padající testy**

Vytvoř `tests/reporting/test_view.py`:

```python
from migration_validator.models.result import CheckResult, MatchInfo, ScopeResult, Severity, Status
from migration_validator.reporting.view import build_view, change_text


def _check(check_id, *, family=None, label="X", value="v", status=Status.PASS,
           mode="state", baseline_value=None, delta=None, message="msg", address=None):
    return CheckResult(
        id=check_id,
        mode=mode,
        status=status,
        severity=Severity.ADVISORY,
        message=message,
        label=label,
        family=family,
        value=value,
        baseline_value=baseline_value,
        delta=delta,
        details={"address": address} if address else {},
    )


def _scope(checks) -> ScopeResult:
    return ScopeResult(
        scope_id="svc:INTERNET-CPE13-NNI:Internet",
        key={"description": "INTERNET-CPE13-NNI", "service_type": "Internet"},
        status=Status.WARN,
        match=MatchInfo(
            status="matched",
            baseline_interfaces=["ge-0/0/2.13"],
            subject_interfaces=["et-0/0/8.13"],
        ),
        checks=checks,
        identity={
            "description": "INTERNET-CPE13-NNI",
            "service_type": "Internet",
            "service_subtype": None,
            "routing_instance": None,
            "ipv4": ["152.11.13.1/30"],
            "ipv6": ["2001:abcd:11:13::a/127"],
            "virtual_gw_v4": [],
            "virtual_gw_v6": [],
        },
    )


def test_sections_are_ordered_common_ipv4_ipv6():
    view = build_view(
        _scope(
            [
                _check("ping_reachability", family=6, label="Ping"),
                _check("interface_state", label="Interface admin status"),
                _check("arp_present", family=4, label="ARP"),
            ]
        )
    )

    assert [section.family for section in view.sections] == [None, 4, 6]


def test_section_carries_its_addresses():
    view = build_view(_scope([_check("arp_present", family=4, label="ARP")]))
    section = next(s for s in view.sections if s.family == 4)

    assert section.addresses == ["152.11.13.1/30"]


def test_empty_family_produces_no_section():
    """Sluzba bez IPv6 nesmi dostat prazdnou IPv6 sekci."""
    view = build_view(_scope([_check("arp_present", family=4, label="ARP")]))

    assert [section.family for section in view.sections] == [4]


def test_ports_come_from_match_info():
    view = build_view(_scope([_check("arp_present", family=4, label="ARP")]))

    assert view.baseline_interfaces == ["ge-0/0/2.13"]
    assert view.subject_interfaces == ["et-0/0/8.13"]


def test_single_address_stays_out_of_the_label():
    """Jedina adresa uz je v hlavicce sekce - v popisku by jen prekazela."""
    view = build_view(
        _scope([_check("arp_present", family=4, label="ARP", value="mac -> 152.11.13.2")])
    )
    section = next(s for s in view.sections if s.family == 4)

    assert section.rows[0].label == "ARP"


def test_second_address_of_the_same_family_enters_the_label():
    """Sluzba s vic rozsahy jedne rodiny - AR-5b, nejen dual-stack."""
    scope = _scope(
        [
            _check("arp_present", family=4, label="ARP", value="mac -> 152.11.13.2",
                   address="152.11.13.1/30"),
            _check("arp_present", family=4, label="ARP", value="mac -> 152.11.20.2",
                   address="152.11.20.1/29"),
        ]
    )
    scope.identity["ipv4"] = ["152.11.13.1/30", "152.11.20.1/29"]

    section = next(s for s in build_view(scope).sections if s.family == 4)

    assert section.addresses == ["152.11.13.1/30", "152.11.20.1/29"]
    assert [row.label for row in section.rows] == [
        "ARP (152.11.13.1/30)",
        "ARP (152.11.20.1/29)",
    ]


def test_state_check_never_says_missing_baseline():
    """STATE check nema baseline z definice - 'bez baseline' by bylo na vsem."""
    row = build_view(_scope([_check("arp_present", family=4, mode="state")])).sections[0].rows[0]

    assert change_text(row, has_baseline=True) == ""


def test_compare_check_without_baseline_says_so():
    row = build_view(
        _scope([_check("bgp_prefix_counts", family=4, mode="compare")])
    ).sections[0].rows[0]

    assert change_text(row, has_baseline=True) == "bez baseline"


def test_identical_values_print_nothing():
    row = build_view(
        _scope([_check("interface_traffic", mode="both", value="460 pps",
                       baseline_value="460 pps")])
    ).sections[0].rows[0]

    assert change_text(row, has_baseline=True) == ""


def test_changed_value_prints_previous_and_delta():
    row = build_view(
        _scope([_check("interface_traffic", mode="both", value="460 pps",
                       baseline_value="520 pps", delta="-12 %")])
    ).sections[0].rows[0]

    assert change_text(row, has_baseline=True) == "bylo 520 pps   -12 %"


def test_no_baseline_at_all_prints_nothing():
    row = build_view(
        _scope([_check("bgp_prefix_counts", family=4, mode="compare")])
    ).sections[0].rows[0]

    assert change_text(row, has_baseline=False) == ""
```

- [ ] **Step 2: Spusť testy a ověř, že padají**

Run: `.venv/bin/pytest tests/reporting/test_view.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'migration_validator.reporting.view'`

- [ ] **Step 3: Napiš `view.py`**

Vytvoř `migration_validator/reporting/view.py`:

```python
"""Prevod vysledku na data reportu.

Oddelene od sazby zamerne: poradi sekci a zarazeni radku do rodin jde
testovat porovnanim datovych struktur, zatimco sirky sloupcu je nutne
testovat proti retezci s mezerami. V jednom souboru by se logika testovala
pres mezery.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from migration_validator.models.result import CheckResult, ScopeResult, Status

# Poradi sekci. None jsou radky, ktere na rodine nezavisi (stav rozhrani).
FAMILY_ORDER = (None, 4, 6)

FAMILY_LABEL = {None: "SPOLECNE", 4: "IPv4", 6: "IPv6"}

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
class Section:
    family: int | None
    addresses: list[str] = field(default_factory=list)
    virtual_gw: list[str] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)


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
        return NO_BASELINE
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
        value=check.value if check.value is not None else check.message,
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
        rows = [_row(check, qualify=len(own) > 1) for check in checks]
        sections.append(
            Section(
                family=family,
                addresses=own,
                virtual_gw=gateways.get(family, []),
                rows=rows,
            )
        )

    match = scope.match
    return ServiceView(
        status=scope.status,
        description=identity.get("description") or scope.scope_id,
        service_type=identity.get("service_type") or "-",
        routing_instance=identity.get("routing_instance"),
        baseline_interfaces=list(match.baseline_interfaces) if match else [],
        subject_interfaces=list(match.subject_interfaces) if match else [],
        worst_message=_worst_message(scope),
        sections=sections,
    )
```

- [ ] **Step 4: Spusť testy a ověř, že prochází**

Run: `.venv/bin/pytest tests/reporting/test_view.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add migration_validator/reporting/view.py tests/reporting/test_view.py
git commit -m "feat(reporting): add ServiceView, the data behind the new report

Section ordering and family assignment are testable as data structures
here; the typesetting layer stays separate so its tests are the only ones
that have to care about whitespace."
```

---

## Task 12: Textový renderer

**Files:**
- Modify: `migration_validator/reporting/text_report.py`
- Modify: `tests/reporting/test_text_report.py`
- Modify: `migration_validator/cli.py` (nápověda k `--detail`, pokud ji zmiňuje)

**Interfaces:**
- Consumes: `build_view`, `change_text`, `Section`, `Row` z Tasku 11
- Produces: `render(result: RunResult, *, detail: bool = False) -> str` — podpis se nemění. `filter_result()` zůstává beze změny.

- [ ] **Step 1: Napiš padající testy**

Nahraď obsah `tests/reporting/test_text_report.py` (ponech stávající testy `filter_result` a `to_json`, pokud tam jsou, a přidej):

```python
from migration_validator.models.result import (
    CheckResult,
    MatchInfo,
    RunResult,
    ScopeResult,
    Severity,
    Status,
)
from migration_validator.reporting.text_report import render


def _check(check_id, status, message, *, label, value, family=None,
           mode="state", baseline_value=None, delta=None):
    return CheckResult(
        id=check_id, mode=mode, status=status, severity=Severity.ADVISORY,
        message=message, label=label, family=family, value=value,
        baseline_value=baseline_value, delta=delta,
    )


def _dual_stack_scope(status=Status.WARN) -> ScopeResult:
    return ScopeResult(
        scope_id="svc:INTERNET-CPE13-NNI:Internet",
        key={"description": "INTERNET-CPE13-NNI", "service_type": "Internet"},
        status=status,
        match=MatchInfo(
            status="matched",
            baseline_interfaces=["ge-0/0/2.13"],
            subject_interfaces=["et-0/0/8.13"],
        ),
        checks=[
            _check("interface_state", Status.PASS, "up", label="Interface admin status", value="Up"),
            _check("arp_present", Status.PASS, "arp ok", label="ARP", family=4,
                   value="0c:00:ef:5e:df:01 -> 152.11.13.2"),
            _check("nd_present", Status.PASS, "nd ok", label="ND", family=6,
                   value="0c:00:ef:5e:df:01 -> 2001:abcd:11:13::b"),
            _check("interface_traffic", Status.WARN, "pokles", label="Interface traffic in",
                   value="460 pps", mode="both", baseline_value="520 pps", delta="-12 %"),
        ],
        identity={
            "description": "INTERNET-CPE13-NNI",
            "service_type": "Internet",
            "service_subtype": None,
            "routing_instance": None,
            "ipv4": ["152.11.13.1/30"],
            "ipv6": ["2001:abcd:11:13::a/127"],
            "virtual_gw_v4": [],
            "virtual_gw_v6": [],
        },
    )


def _result(scopes, *, baseline=True) -> RunResult:
    return RunResult(
        evaluated_at="2026-07-28T11:40:02Z",
        subject={"address": "172.20.20.5", "phase": "post-migration", "captured_at": "x"},
        baseline=(
            {"address": "172.20.20.4", "phase": "pre-migration", "captured_at": "y"}
            if baseline
            else None
        ),
        summary={"pass": 1, "warn": 1, "fail": 0, "skip": 0,
                 "scopes_matched": 1, "unmatched_baseline": 0, "unmatched_subject": 0},
        scopes=scopes,
    )


def test_ipv4_section_comes_before_ipv6():
    output = render(_result([_dual_stack_scope()]))

    assert output.index("-- IPv4") < output.index("-- IPv6")


def test_section_header_carries_the_address():
    output = render(_result([_dual_stack_scope()]))

    assert "-- IPv4  152.11.13.1/30" in output
    assert "-- IPv6  2001:abcd:11:13::a/127" in output


def test_long_ipv6_value_is_not_truncated():
    """Orezana IPv6 adresa je horsi nez zadna."""
    output = render(_result([_dual_stack_scope()]))

    assert "0c:00:ef:5e:df:01 -> 2001:abcd:11:13::b" in output


def test_columns_hold_the_line_across_sections():
    """Sirky se pocitaji z celeho bloku, ne z kazde sekce zvlast."""
    output = render(_result([_dual_stack_scope()]))
    data_lines = [
        line for line in output.splitlines()
        if line.startswith((" PASS |", " WARN |", " FAIL |", " SKIP |"))
    ]
    positions = {line.index(" : ") for line in data_lines}

    assert len(positions) == 1, f"sloupec s hodnotou se lame: {positions}"


def test_passing_service_is_collapsed_by_default():
    output = render(_result([_dual_stack_scope(status=Status.PASS)]))

    assert "-- IPv4" not in output


def test_failing_service_is_expanded_by_default():
    output = render(_result([_dual_stack_scope(status=Status.FAIL)]))

    assert "-- IPv4" in output


def test_detail_expands_passing_service():
    output = render(_result([_dual_stack_scope(status=Status.PASS)]), detail=True)

    assert "-- IPv4" in output


def test_change_column_is_absent_without_baseline():
    output = render(_result([_dual_stack_scope()], baseline=False))

    assert "ZMENA" not in output


def test_change_column_shows_previous_value():
    output = render(_result([_dual_stack_scope()]))

    assert "bylo 520 pps" in output


def test_ports_are_in_the_summary_row():
    output = render(_result([_dual_stack_scope()]))

    assert "ge-0/0/2.13" in output
    assert "et-0/0/8.13" in output


def _vgw_scope() -> ScopeResult:
    """EVPN-VLAN-AWARE-INTERNET z laborky: irb.14 ma virtual gateway."""
    return ScopeResult(
        scope_id="svc:EVPN-VLAN-AWARE-INTERNET:Internet",
        key={"description": "EVPN-VLAN-AWARE-INTERNET", "service_type": "Internet"},
        status=Status.FAIL,
        match=MatchInfo(
            status="matched",
            baseline_interfaces=["ge-0/0/5.0"],
            subject_interfaces=["irb.14"],
        ),
        checks=[
            _check("arp_present", Status.FAIL, "zadny zaznam", label="ARP", family=4,
                   value="zadny zaznam"),
        ],
        identity={
            "description": "EVPN-VLAN-AWARE-INTERNET",
            "service_type": "Internet",
            "service_subtype": None,
            "routing_instance": None,
            "ipv4": ["152.11.14.2/29"],
            "ipv6": [],
            "virtual_gw_v4": ["152.11.14.1"],
            "virtual_gw_v6": [],
        },
    )


def test_virtual_gateway_is_shown_in_the_section_header():
    output = render(_result([_vgw_scope()]))

    assert "-- IPv4  152.11.14.2/29   VGW 152.11.14.1" in output


def test_service_without_ipv6_has_no_ipv6_section():
    output = render(_result([_vgw_scope()]))

    assert "-- IPv6" not in output
```

- [ ] **Step 2: Spusť testy a ověř, že padají**

Run: `.venv/bin/pytest tests/reporting/test_text_report.py -v`
Expected: FAIL — `AssertionError` na chybějícím `-- IPv4` v přehledovém výpisu

- [ ] **Step 3: Přepiš renderer**

Nejdřív sjednoť symbol pro PASS. Dnes je `Status.PASS: "OK "`, ale spec i celý nový výstup používají `PASS` a golden testy na to spoléhají:

```python
SYMBOL = {
    Status.PASS: "PASS",
    Status.WARN: "WARN",
    Status.FAIL: "FAIL",
    Status.SKIP: "SKIP",
}
```

Pak nahraď funkce `_worst_message`, `_detail_lines` a `render` (funkci `filter_result` ponech beze změny):

```python
from migration_validator.reporting.view import Section, ServiceView, build_view, change_text

FAMILY_TITLE = {4: "IPv4", 6: "IPv6"}


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

    label_width = max([len(row.label) for row in rows] + [len("CHECK")])
    subject_port = view.subject_interfaces[0] if view.subject_interfaces else "-"
    value_title = f"POST ({subject_port})" if has_baseline else "HODNOTA"
    value_width = max([len(row.value) for row in rows] + [len(value_title)])
    change_width = max([len(text) for text in changes.values()] + [0])

    def line(status: str, label: str, value: str, change: str) -> str:
        text = f" {status:<4} | {label:<{label_width}} : {value:<{value_width}}"
        if not has_baseline:
            return text.rstrip()
        return f"{text} | {change}".rstrip()

    width = 1 + 4 + 3 + label_width + 3 + value_width
    if has_baseline:
        width += 3 + change_width

    baseline_port = view.baseline_interfaces[0] if view.baseline_interfaces else "-"
    ports = (
        f"{baseline_port} -> {subject_port}" if has_baseline else subject_port
    )
    instance = view.routing_instance or "-"

    lines = [
        "=" * width,
        f" {SYMBOL[view.status].strip():<4}  {view.description}   "
        f"{view.service_type}   {ports}   RI: {instance}",
        "=" * width,
        line("STAV", "CHECK", value_title, f"ZMENA PROTI {baseline_port}"),
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

    lines.append(
        f"{'STAV':<5} {'SLUZBA':<26} {'TYP':<9} {'STARY PORT':<13} "
        f"{'NOVY PORT':<13} {'RI':<17} NALEZ"
    )
    for _scope, view in views:
        old_port = view.baseline_interfaces[0] if view.baseline_interfaces else "-"
        new_port = view.subject_interfaces[0] if view.subject_interfaces else "-"
        lines.append(
            f"{SYMBOL[view.status]:<5} {view.description:<26} {view.service_type:<9} "
            f"{old_port:<13} {new_port:<13} {view.routing_instance or '-':<17} "
            f"{view.worst_message}".rstrip()
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
    for side in ("baseline", "subject"):
        for item in result.unmatched[side]:
            label = item.get("description") or item["scope_id"]
            service_type = item.get("service_type") or "-"
            lines.append(f"  {side:<9} {label:<32.32} ({service_type})  {item['reason']}")

    return "\n".join(lines) + "\n"
```

Uprav i docstring modulu:

```python
"""Terminalovy vystup.

Vychozi vypis je souhrn, radek na sluzbu a plny blok u sluzeb se stavem
WARN nebo FAIL. --detail rozbali bloky u vsech vcetne PASS.

Sekce NESPAROVANO se vypisuje vzdy, i kdyz je vsechno ostatni zelene, a
filtrovani se na ni nevztahuje - je to hlavni pojistka proti prehlednuti
nezmigrovane sluzby.
"""
```

- [ ] **Step 4: Spusť testy a ověř, že prochází**

Run: `.venv/bin/pytest tests/reporting -v`
Expected: PASS

- [ ] **Step 5: Ověř výstup okem**

Run: `.venv/bin/pytest tests/reporting/test_text_report.py -v -s -k dual` a doplň si dočasně `print(output)` do jednoho testu, nebo:

```bash
.venv/bin/python -c "
from tests.reporting.test_text_report import _dual_stack_scope, _result
from migration_validator.reporting.text_report import render
print(render(_result([_dual_stack_scope()]), detail=True))
"
```

Expected: blok se svislými čarami, které drží linku napříč sekcí IPv4 i IPv6.

- [ ] **Step 6: Spusť celou sadu**

Run: `.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add migration_validator/reporting/text_report.py tests/reporting/test_text_report.py
git commit -m "feat(reporting): block per service with per-family sections

Column widths are computed from the whole block and nothing is truncated
- the old renderer cut check ids at 22 characters, which would have
chopped IPv6 addresses in half.

Expansion is driven by status rather than interactivity: a terminal
cannot be clicked, but detail is wanted exactly where something failed."
```

---

## Task 13: Regenerace inventory, fixtures a e2e ověření

**Files:**
- Modify: `172.20.20.4.yml`, `172.20.20.5.yml` (regenerace)
- Modify: `tests/fixtures/172.20.20.4.yml`, `tests/fixtures/172.20.20.5.yml` (regenerace)
- Modify: `docs/cs/files/*.md`, `docs/en/files/*.md` — soubory popisující parsery, inventory, ping, reporting
- Modify: `README.md`, pokud zmiňuje formát inventory

**Interfaces:**
- Consumes: vše z Tasků 1–12
- Produces: běžící nástroj nad reálnými daty

- [ ] **Step 1: Regeneruj inventory z laborky**

```bash
eval "$(grep '^export MIG_LAB_PASSWORD=' ~/.bashrc)"
./mx_parser.py  -u admin --auth password 172.20.20.4
./evo_parser.py -u admin --auth password 172.20.20.5
```

Ověř, že soubory mají nový tvar:

```bash
head -20 172.20.20.4.yml
grep -c "ipv4_address\|ipv6_address" 172.20.20.4.yml 172.20.20.5.yml
grep -n "schema_version" 172.20.20.4.yml 172.20.20.5.yml
grep -c "ip_address:" 172.20.20.4.yml 172.20.20.5.yml
```

Expected: `schema_version: 2` na prvním řádku, žádný výskyt holého `ip_address:`.

- [ ] **Step 2: Zkopíruj je jako test fixtures**

```bash
cp 172.20.20.4.yml tests/fixtures/172.20.20.4.yml
cp 172.20.20.5.yml tests/fixtures/172.20.20.5.yml
```

- [ ] **Step 3: Spusť celou sadu**

Run: `.venv/bin/pytest -q`
Expected: PASS, včetně `tests/collectors/test_conformance.py`, který jede nad těmito inventory a nahraným XML.

- [ ] **Step 4: Ověř sběr proti živé laborce**

```bash
eval "$(grep '^export MIG_LAB_PASSWORD=' ~/.bashrc)"
.venv/bin/mig-validate capture \
    --device 172.20.20.4 --inventory 172.20.20.4.yml \
    --username admin --auth password --password "$MIG_LAB_PASSWORD" \
    --phase pre-migration --output runs/ipv6/pre.json
.venv/bin/mig-validate capture \
    --device 172.20.20.5 --inventory 172.20.20.5.yml \
    --username admin --auth password --password "$MIG_LAB_PASSWORD" \
    --phase post-migration --output runs/ipv6/post.json
```

Ověř, že snapshot obsahuje ND fakta a IPv6 ping cíle:

```bash
.venv/bin/python -c "
import json
snap = json.load(open('runs/ipv6/post.json'))
print('schema_version:', snap['schema_version'])
print('ND zaznamu:', len(snap['facts']['nd']))
pings = snap['probes']['ping']
print('ping cilu:', len(pings))
print('rodiny:', sorted({p.get('family') for p in pings}))
for p in pings:
    print(' ', p['family'], p['target'], p['resolved_from'], p['received'], '/', p['sent'])
"
```

Expected: `schema_version: 2`, nenulový počet ND záznamů, mezi rodinami je i `6`.

- [ ] **Step 5: Ověř report okem**

```bash
.venv/bin/mig-validate evaluate --snapshot runs/ipv6/post.json --baseline runs/ipv6/pre.json
```

Expected: souhrn, tabulka služeb, bloky u nezelených služeb, v blocích sekce IPv4 před IPv6, sloupce drží linku, nic ořezaného.

```bash
.venv/bin/mig-validate evaluate --snapshot runs/ipv6/post.json --baseline runs/ipv6/pre.json --detail | head -60
.venv/bin/mig-validate evaluate --snapshot runs/ipv6/post.json | head -40
```

Expected: `--detail` rozbalí i PASS služby; běh bez `--baseline` nemá sloupec ZMENA.

- [ ] **Step 6: Ověř, že ping opravdu zrychlil**

```bash
eval "$(grep '^export MIG_LAB_PASSWORD=' ~/.bashrc)"
time .venv/bin/mig-validate capture \
    --device 172.20.20.5 --inventory 172.20.20.5.yml \
    --username admin --auth password --password "$MIG_LAB_PASSWORD" \
    --phase post-migration --output /tmp/rapid-check.json
```

Expected: celkový čas výrazně nižší než před změnou (5 paketů na cíl zabere ~0,3 s místo ~5 s).

- [ ] **Step 7: Aktualizuj dokumentaci**

Projdi a uprav místa, která popisují změněné chování:

```bash
grep -rln "ip_address\|virtual_gw_ip_address\|local_addresses\|prefixes" docs/cs docs/en README.md
```

Uprav zejména:
- `docs/{cs,en}/files/parsers.md` — nová pole a `schema_version`
- `docs/{cs,en}/files/models.md` — `ServiceEntry`, `Selectors`, `ScopeResult.identity`
- `docs/{cs,en}/files/collectors.md` — nový collector `nd`
- `docs/{cs,en}/files/probes.md` — `rapid`, IPv6 cíle, link-local, fallback
- `docs/{cs,en}/files/checks.md` — `nd_present`, jemnější findingy
- `docs/{cs,en}/files/reporting.md` — nový formát, `view.py`, sloupec ZMENA
- `docs/{cs,en}/README.md` — ukázka výstupu, pokud tam je

Anglická dokumentace cituje české řetězce doslova — zachovej to.

- [ ] **Step 8: Spusť celou sadu naposledy**

Run: `.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add 172.20.20.4.yml 172.20.20.5.yml tests/fixtures/ docs/ README.md
git commit -m "chore: regenerate inventories and fixtures, update docs

Verified end to end against the lab: ND facts are collected, IPv6 ping
targets resolve from them, and capture is markedly faster with rapid."
```

---

## Poznámky pro implementaci

**Pořadí tasků je závazné.** Tasky 1–5 mění schéma dat a všechno ostatní na nich stojí. Task 6 musí předcházet 7 a 8. Tasky 9 a 10 jsou na sobě nezávislé a jdou prohodit. Tasky 11 a 12 potřebují hotové 5, 7, 9 a 10, jinak nebude co renderovat.

**Testy budou padat i mimo měněný task.** Task 4 přejmenuje pole, na které sahá `probes/ping.py` — celá sada bude červená až do Tasku 8. To je očekávané; každý task má vlastní cílený příkaz `pytest`, celou sadu spouštěj až tam, kde to plán říká.

**Co plán vědomě nedělá:** BFD a statické routy. Mají vlastní spec. V blocích reportu se do té doby nezobrazí žádný jejich řádek — ukázka v návrhu je značená jako budoucí.
