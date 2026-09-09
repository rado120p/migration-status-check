# E-LAN `local` (globální bridge-domain / vlan) — implementační plán

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Access port v globální bridge-domain (MX) / vlan (EVO), tedy v instanci `default-switch`, se klasifikuje jako E-LAN `local`, dostane vlastní blok (MAC count, stav rozhraní, deaktivace) hned za blokem své IRB a per-port běh přitáhne IRB polovinu služby.

**Architecture:** Parser doplní vyhledání globální domény pro unit bez routing-instance a novou klasifikační větev; inventory schéma 10 poprvé zapisuje `l3_interface`. Scope `local` vybírá `evpn_mac["default-switch"]`, linker přidává konfigurační vazbu L2→IRB přes selektor `l2_interfaces` L3 scope. Collector `evpn_mac` přestane `default-switch` zahazovat; EVPN-only checky se na `local` neaplikují.

**Tech Stack:** Python 3.13, lxml, PyYAML, pytest (`.venv/bin/pytest`), laborka containerlab (MX1-POP1 `172.20.20.4` junos, PTX1-POP1 `172.20.20.5` junos-evo, user `admin`, heslo `$MIG_LAB_PASSWORD`).

**Spec:** `docs/superpowers/specs/2026-09-09-local-bridge-domain-elan-design.md`

## Global Constraints

- Subtype se jmenuje přesně `local` (E-LAN). Detection reason: `Rozhraní je členem globální bridge-domain / vlan (default-switch), bez EVPN instance.`
- Žádný nový collector ani RPC. Snapshot `SCHEMA_VERSION` zůstává 13. Inventory `INVENTORY_SCHEMA_VERSION` = 10.
- `default-switch` se do `Selectors.routing_instances` nikdy nedává.
- Řádek „IRB interface" na L2 `local` bloku se nedělá.
- Label checku `evpn_mac_count` je `MAC count` (id zůstává `evpn_mac_count`).
- Důvod deaktivace pro `local` bez RI: `bridge-domain deactivated`.
- Laboratorní inventory fixtures se přegenerují z laborky, nikdy ručně.
- Tvrzení o zabitém mutantu (v docstringu testu nebo v commit message) se ověřuje spuštěním mutanta po přegenerování fixtures (Task 3), ne úsudkem.
- Testy se spouští `.venv/bin/pytest` (addopts `-q`, testpaths `tests`). Před každým commitem musí projít celá sada (`.venv/bin/pytest`), pokud task výslovně neříká, že po něm zůstává známý červený test do dalšího tasku.
- Heslo laborky: `eval "$(grep '^export MIG_LAB_PASSWORD=' ~/.bashrc)"` na začátku každého příkazu, který mluví s laborkou (v `~/.bashrc` je pod guardem pro neinteraktivní shell).
- Commit messages končí:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_016oeksRHo5p1vm6wJdbS4Fk
  ```

---

## Mapa souborů

| Soubor | Změna |
|---|---|
| `migration_validator/parsers/core.py` | `BridgeDomain.active`; `_parse_l2_domain_container` čte deaktivaci; `_find_interface_bridge_domains` větev pro globální domény; `_detect_service` větev E-LAN `local`; `_classify_interface` přenáší aktivitu domény; `clean_service_dict` zapisuje `l3_interface` |
| `migration_validator/models/inventory.py` | `ServiceEntry.l3_interface`; `INVENTORY_SCHEMA_VERSION = 10` + changelog |
| `migration_validator/models/scope.py` | `deactivation_reason` pro `local`; `select` vybírá `evpn_mac["default-switch"]` pro `local` |
| `migration_validator/scoping/linker.py` | konfigurační vazba `local` L2 → L3 |
| `migration_validator/collectors/evpn.py` | `EvpnMacCollector.SYSTEM_INSTANCES` prázdné |
| `migration_validator/checks/evpn.py` | `excluded_subtypes = {"local"}` na ESI + instance checku; label `MAC count` |
| `tests/parsers/test_local_bridge_domain.py` | nový: klasifikace, vazby, deaktivace (MX + EVO) |
| `tests/models/test_inventory.py`, `tests/runs/test_services.py`, `tests/models/test_scope.py`, `tests/scoping/test_linker.py`, `tests/collectors/test_evpn.py`, `tests/checks/test_evpn.py`, `tests/conftest.py` | úpravy/rozšíření |
| `tests/fixtures/172.20.20.4.yml`, `tests/fixtures/172.20.20.5.yml`, `172.20.20.4.yml`, `172.20.20.5.yml` | přegenerované z laborky (schema 10) |
| `docs/cs/files/parsers.md`, `docs/cs/files/models.md`, `docs/en/files/parsers.md`, `docs/en/files/models.md` | subtype `local`, pole `l3_interface`, changelog 9 → 10 |

Pořadí tasků je závazné: Task 2 zvedne schéma a tím zčervenají testy nad laboratorními fixtures (schema 9), které zase zezelenají v Tasku 3 po regeneraci.

---

### Task 1: Parser — klasifikace E-LAN `local` a aktivita domény

**Files:**
- Modify: `migration_validator/parsers/core.py:93-104` (`BridgeDomain`), `:898-944` (`_parse_l2_domain_container`), `:1176-1238` (`_classify_interface`), `:1555-1594` (`_find_interface_bridge_domains`), `:1706-1724` (`_detect_service`, před blokem „E-LAN EVPN")
- Test: `tests/parsers/test_local_bridge_domain.py` (nový)

**Interfaces:**
- Consumes: `InterfaceConfig.name/physical_name/families/encapsulation`, `self.global_l2_domains: list[BridgeDomain]`, `self._is_inactive(node)`, `self._is_layer2(interface)`.
- Produces: `BridgeDomain.active: bool` (default `True`); `InterfaceService` pro access port v globální doméně se `service_type="E-LAN"`, `service_subtype="local"`, `bridge_domain`, `customer_vlan`, `l3_interface`, `routing_instance=None`, `routing_instance_active=<aktivita domén>`.

- [ ] **Step 1: Napiš failing testy**

Vytvoř `tests/parsers/test_local_bridge_domain.py`:

```python
"""E-LAN `local`: access port v globalni bridge-domain (MX) / vlan (EVO),
tedy v instanci default-switch (spec 2026-09-09). Stanzy zrcadli laborku
MX1-POP1 / PTX1-POP1 (2026-09-09)."""

from __future__ import annotations

import pytest
from lxml import etree

from migration_validator.parsers.evo import JunosEvoAcxServiceParser
from migration_validator.parsers.mx import JunosServiceParser

# ge-0/0/2.12 je v domene, ge-0/0/2.5 je L2 bez domeny, ge-0/0/2.77 ma
# vlan-id 12 ale v domene neni (prekryv VLAN nesmi stacit).
INTERFACES = """
  <interfaces>
    <interface>
      <name>ge-0/0/2</name>
      <unit>
        <name>12</name>
        <description>NGMVPN-IGMP-RECEIVER</description>
        <encapsulation>vlan-bridge</encapsulation>
        <vlan-id>12</vlan-id>
      </unit>
      <unit>
        <name>5</name>
        <description>L2-BEZ-DOMENY</description>
        <encapsulation>vlan-bridge</encapsulation>
        <vlan-id>5</vlan-id>
      </unit>
      <unit>
        <name>77</name>
        <description>TRUNK-STEJNA-VLAN</description>
        <encapsulation>vlan-bridge</encapsulation>
        <vlan-id>12</vlan-id>
      </unit>
    </interface>
    <interface>
      <name>irb</name>
      <unit>
        <name>2</name>
        <description>NGMVPN receivers POP1</description>
        <family><inet><address><name>10.12.11.2/30</name></address></inet></family>
      </unit>
    </interface>
  </interfaces>
  <routing-instances>
    <instance>
      <name>NGMVPN-IGMP-RECEIVER</name>
      <instance-type>vrf</instance-type>
      <interface><name>irb.2</name></interface>
    </instance>
  </routing-instances>
"""

MX_DOMAINS = """
  <bridge-domains{attr}>
    <domain{domain_attr}>
      <name>BD-NGMVPN-IGMP-RECEIVER</name>
      <domain-type>bridge</domain-type>
      <vlan-id>12</vlan-id>
      <interface><name>ge-0/0/2.12</name></interface>
      <routing-interface>irb.2</routing-interface>
      <protocols><igmp-snooping><version>3</version></igmp-snooping></protocols>
    </domain>
    <domain>
      <name>BD-JINA</name>
      <domain-type>bridge</domain-type>
      <vlan-id>99</vlan-id>
    </domain>
  </bridge-domains>
"""

EVO_DOMAINS = """
  <vlans{attr}>
    <vlan{domain_attr}>
      <name>VL-NGMVPN-IGMP-RECEIVER</name>
      <vlan-id>12</vlan-id>
      <interface><name>ge-0/0/2.12</name></interface>
      <l3-interface>irb.2</l3-interface>
    </vlan>
    <vlan>
      <name>VL-JINA</name>
      <vlan-id>99</vlan-id>
    </vlan>
    <vlan>
      <name>default</name>
      <vlan-id>1</vlan-id>
    </vlan>
  </vlans>
"""


def _config(domains: str, attr: str = "", domain_attr: str = "") -> etree._Element:
    body = domains.format(attr=attr, domain_attr=domain_attr)
    return etree.fromstring(f"<configuration>{INTERFACES}{body}</configuration>")


CASES = (
    pytest.param(JunosServiceParser, MX_DOMAINS, "BD-NGMVPN-IGMP-RECEIVER", id="mx"),
    pytest.param(JunosEvoAcxServiceParser, EVO_DOMAINS, "VL-NGMVPN-IGMP-RECEIVER", id="evo"),
)


def _by_iface(parser_class, config):
    return {s.interface: s for s in parser_class(config).parse()}


@pytest.mark.parametrize(("parser_class", "domains", "domain"), CASES)
def test_access_port_in_global_domain_is_elan_local(parser_class, domains, domain):
    port = _by_iface(parser_class, _config(domains))["ge-0/0/2.12"]
    assert (port.service_type, port.service_subtype) == ("E-LAN", "local")
    assert port.detection_confidence == "high"
    assert port.routing_instance is None
    assert port.bridge_domain == [domain]
    assert port.customer_vlan == ["12"]
    assert port.l3_interface == ["irb.2"]
    assert port.routing_instance_active is True
    assert port.interface_active is True
    assert port.detection_reason == [
        "Rozhraní je členem globální bridge-domain / vlan (default-switch), bez EVPN instance."
    ]


@pytest.mark.parametrize(("parser_class", "domains", "domain"), CASES)
def test_irb_side_is_unchanged(parser_class, domains, domain):
    irb = _by_iface(parser_class, _config(domains))["irb.2"]
    assert irb.service_type == "IPVPN"
    assert irb.l2_interface == ["ge-0/0/2.12"]
    assert irb.bridge_domain == [domain]


@pytest.mark.parametrize(("parser_class", "domains", "domain"), CASES)
def test_l2_port_outside_any_domain_stays_unknown(parser_class, domains, domain):
    port = _by_iface(parser_class, _config(domains))["ge-0/0/2.5"]
    assert (port.service_type, port.service_subtype) == ("Unknown", "layer2")
    assert port.l3_interface == []


@pytest.mark.parametrize(("parser_class", "domains", "domain"), CASES)
def test_vlan_overlap_alone_does_not_join_global_domain(parser_class, domains, domain):
    port = _by_iface(parser_class, _config(domains))["ge-0/0/2.77"]
    assert (port.service_type, port.service_subtype) == ("Unknown", "layer2")
    assert port.bridge_domain == []


@pytest.mark.parametrize(("parser_class", "domains", "domain"), CASES)
def test_deactivated_container_flags_local_service_not_irb(parser_class, domains, domain):
    by_iface = _by_iface(parser_class, _config(domains, attr=' inactive="inactive"'))
    port = by_iface["ge-0/0/2.12"]
    assert (port.service_type, port.service_subtype) == ("E-LAN", "local")
    assert port.routing_instance_active is False
    assert port.interface_active is True
    # IRB deaktivace domeny nededi - config ji nedeaktivoval.
    assert by_iface["irb.2"].routing_instance_active is True
    assert by_iface["irb.2"].interface_active is True


@pytest.mark.parametrize(("parser_class", "domains", "domain"), CASES)
def test_deactivated_single_domain_flags_only_its_port(parser_class, domains, domain):
    port = _by_iface(parser_class, _config(domains, domain_attr=' inactive="inactive"'))[
        "ge-0/0/2.12"
    ]
    assert port.routing_instance_active is False


@pytest.mark.parametrize(("parser_class", "domains", "domain"), CASES)
def test_active_domain_active_flag(parser_class, domains, domain):
    parser = parser_class(_config(domains))
    parser.parse()
    by_name = {d.name: d for d in parser.global_l2_domains}
    assert by_name[domain].active is True
    parser = parser_class(_config(domains, attr=' inactive="inactive"'))
    parser.parse()
    assert all(d.active is False for d in parser.global_l2_domains)
```

- [ ] **Step 2: Ověř, že testy padají**

Run: `.venv/bin/pytest tests/parsers/test_local_bridge_domain.py -v`
Expected: FAIL — `test_access_port_in_global_domain_is_elan_local` na `("Unknown", "layer2") != ("E-LAN", "local")`, `test_active_domain_active_flag` na `AttributeError: 'BridgeDomain' object has no attribute 'active'`.

- [ ] **Step 3: `BridgeDomain.active` a čtení deaktivace**

V `migration_validator/parsers/core.py` doplň do dataclass `BridgeDomain` (za `routing_interface`):

```python
    routing_interface: str | None = None
    # Deaktivace domeny (i zdedena z kontejneru bridge-domains / vlans).
    # U E-LAN `local` je domena kontejner sluzby stejne jako RI u EVPN,
    # takze jeji priznak konci v routing_instance_active (spec 2026-09-09).
    active: bool = True
```

V `_parse_l2_domain_container` doplň při konstrukci:

```python
            domains.append(
                BridgeDomain(
                    name=domain_name,
                    vlan_ids=vlan_ids,
                    vlan_id_list=vlan_id_list,
                    interfaces=interfaces,
                    routing_interface=routing_interface,
                    active=not self._is_inactive(domain_node),
                )
            )
```

- [ ] **Step 4: Vyhledání globální domény pro unit bez instance**

V `_find_interface_bridge_domains` nahraď úvodní `if instance is None: return []`:

```python
        if instance is None:
            # Unit bez routing-instance muze byt access port globalni
            # bridge-domain (MX) / vlan (EVO) = instance default-switch.
            # Jen explicitni clenstvi: default-switch na EVO nese i
            # `default` (VLAN 1) a hadani trunku podle prekryvu VLAN by
            # privesilo nahodne porty (spec 2026-09-09).
            return [
                domain
                for domain in self.global_l2_domains
                if interface.name in domain.interfaces
                or interface.physical_name in domain.interfaces
            ]
```

- [ ] **Step 5: Klasifikační větev E-LAN `local`**

V `_detect_service` vlož **před** komentářový blok `# E-LAN EVPN` (tedy za VPLS větev):

```python
        # --------------------------------------------------------------
        # E-LAN local: globalni bridge-domain / vlan (default-switch)
        # --------------------------------------------------------------

        if instance is None and bridge_domains and self._is_layer2(interface):
            reasons.append(
                "Rozhraní je členem globální bridge-domain / vlan (default-switch), "
                "bez EVPN instance."
            )

            return ("E-LAN", "local", "high", reasons)
```

- [ ] **Step 6: Aktivita domény do `routing_instance_active`**

V `_classify_interface` nahraď `routing_instance_active=instance.active if instance else True,`:

```python
            routing_instance_active=(
                instance.active
                if instance
                else all(domain.active for domain in bridge_domains)
            ),
```

(`all([])` je `True`, takže unit bez instance i bez domény zůstává aktivní.)

- [ ] **Step 7: Ověř, že testy procházejí, a celou sadu**

Run: `.venv/bin/pytest tests/parsers/test_local_bridge_domain.py -v`
Expected: PASS (7 testů × 2 platformy).

Run: `.venv/bin/pytest`
Expected: PASS. Pokud některý stávající test padne kvůli tomu, že L2 port v globální doméně dřív byl `Unknown` (hledej v `tests/parsers/` a `tests/runs/`), uprav jeho očekávání na `E-LAN`/`local` — jde o zamýšlenou změnu, ne o regresi.

- [ ] **Step 8: Commit**

```bash
git add migration_validator/parsers/core.py tests/parsers/test_local_bridge_domain.py
git commit -m "feat(parser): E-LAN local for access ports in global bridge-domains / vlans"
```

---

### Task 2: Inventory schéma 10 — `l3_interface` v YAML a v `ServiceEntry`

**Files:**
- Modify: `migration_validator/parsers/core.py:2038-2062` (`clean_service_dict`)
- Modify: `migration_validator/models/inventory.py:51-137` (`ServiceEntry`), `:145-157` (changelog + verze), `:160-188` (docstring `load_inventory`)
- Test: `tests/models/test_inventory.py`, `tests/runs/test_services.py`

**Interfaces:**
- Produces: `ServiceEntry.l3_interface: list[str]` (default `[]`), klíč `l3_interface` v inventory YAML za `customer_vlan`, `INVENTORY_SCHEMA_VERSION == 10`.

- [ ] **Step 1: Failing testy**

V `tests/models/test_inventory.py` nahraď oba výskyty `schema_version: 9` za `schema_version: 10` a přidej:

```python
def test_entry_round_trips_l3_interface():
    entry = ServiceEntry.from_dict(
        {
            "interface": "ge-0/0/2.12",
            "service_type": "E-LAN",
            "service_subtype": "local",
            "l3_interface": ["irb.2"],
        }
    )
    assert entry.l3_interface == ["irb.2"]
    assert entry.to_dict()["l3_interface"] == ["irb.2"]


def test_entry_without_l3_interface_defaults_to_empty():
    entry = ServiceEntry.from_dict({"interface": "irb.2", "service_type": "IPVPN"})
    assert entry.l3_interface == []


def test_schema_version_is_10():
    assert INVENTORY_SCHEMA_VERSION == 10
```

V `tests/runs/test_services.py` přidej za `test_port_filter_pulls_irb_of_shared_vlan`:

```python
GLOBAL_BD_WITH_IRB = """
<configuration>
  <interfaces>
    <interface>
      <name>ge-0/0/2</name>
      <unit>
        <name>12</name>
        <description>NGMVPN-IGMP-RECEIVER</description>
        <encapsulation>vlan-bridge</encapsulation>
        <vlan-id>12</vlan-id>
      </unit>
    </interface>
    <interface>
      <name>irb</name>
      <unit>
        <name>2</name>
        <family><inet><address><name>10.12.11.2/30</name></address></inet></family>
      </unit>
    </interface>
  </interfaces>
  <routing-instances>
    <instance>
      <name>NGMVPN-IGMP-RECEIVER</name>
      <instance-type>vrf</instance-type>
      <interface><name>irb.2</name></interface>
    </instance>
  </routing-instances>
  <bridge-domains>
    <domain>
      <name>BD-NGMVPN-IGMP-RECEIVER</name>
      <domain-type>bridge</domain-type>
      <vlan-id>12</vlan-id>
      <interface><name>ge-0/0/2.12</name></interface>
      <routing-interface>irb.2</routing-interface>
    </domain>
  </bridge-domains>
</configuration>
"""


def test_port_filter_pulls_irb_of_global_bridge_domain(tmp_path, monkeypatch):
    """Lab 2026-09-09: IRB v MVPN VRF + access port v globalni bridge-domain.

    Per-port beh ge-0/0/2 bez irb.2 nema L3 polovinu sluzby - presne to,
    co se stalo pri per-port migraci (spec 2026-09-09).
    """
    _fake_retrieve_configuration(monkeypatch, GLOBAL_BD_WITH_IRB)

    output_path = tmp_path / "inv.yml"
    generate_inventory(object(), "junos", output_path, port="ge-0/0/2")

    entries = {
        s["interface"]: s for s in yaml.safe_load(output_path.read_text())["interfaces"]
    }
    assert "irb.2" in entries
    assert entries["ge-0/0/2.12"]["service_subtype"] == "local"
    assert entries["ge-0/0/2.12"]["l3_interface"] == ["irb.2"]
```

- [ ] **Step 2: Ověř, že padají**

Run: `.venv/bin/pytest tests/models/test_inventory.py tests/runs/test_services.py -v`
Expected: FAIL — `test_load_inventory` na `schema_version 10, nastroj umi 9`, `test_entry_round_trips_l3_interface` na `AttributeError`, `test_port_filter_pulls_irb_of_global_bridge_domain` na `KeyError: 'l3_interface'`.

- [ ] **Step 3: `ServiceEntry.l3_interface`**

V `migration_validator/models/inventory.py` doplň do `ServiceEntry` za `customer_vlan`:

```python
    customer_vlan: list[str] = field(default_factory=list)
    # irb protejsky domen tohoto L2 unitu (schema 10). Do te doby zil jen
    # v procesu parseru pro port filtr; ted ho nese i YAML, aby bylo z
    # inventory videt, ke ktere IRB access port patri.
    l3_interface: list[str] = field(default_factory=list)
```

Do `from_dict` doplň `l3_interface=_as_list(data.get("l3_interface")),` za `customer_vlan=...` a do `to_dict` `"l3_interface": list(self.l3_interface),` za `"customer_vlan"`.

- [ ] **Step 4: Verze 10 + changelog**

Nahraď `INVENTORY_SCHEMA_VERSION = 9` a doplň komentář nad ním:

```python
# 10: subtype E-LAN "local" (access port v globalni bridge-domain / vlan,
#     instance default-switch, spec 2026-09-09) a pole l3_interface (irb
#     protejsky domen L2 unitu) se poprve zapisuje do YAML. Stara inventory
#     by port nesla jako Unknown/layer2 - bez scope, bez vazby na IRB.
INVENTORY_SCHEMA_VERSION = 10
```

Do docstringu `load_inventory` přidej odstavec za „Verze 9 …":

```
    Verze 10 pridala subtype E-LAN "local" a pole l3_interface. Tolerantni
    cteni stare (v9) inventory by access port v globalni domene nechalo
    jako Unknown/layer2 - zadny scope, zadny blok v reportu, a per-port
    beh by nepritahl IRB polovinu sluzby, aniz by to bylo videt.
```

- [ ] **Step 5: `l3_interface` do YAML**

V `clean_service_dict` (`parsers/core.py`) vlož `"l3_interface",` mezi `"customer_vlan",` a `"l2_interface",`.

- [ ] **Step 6: Testy**

Run: `.venv/bin/pytest tests/models/test_inventory.py tests/runs/test_services.py tests/parsers -v`
Expected: PASS.

Run: `.venv/bin/pytest`
Expected: **červené jen** testy načítající laboratorní fixtures `tests/fixtures/172.20.20.4.yml` / `172.20.20.5.yml` (schema 9): `tests/collectors/test_conformance.py`, `tests/scoping/test_builder.py::test_real_inventory_files_produce_expected_scope_counts` a případné další, které je čtou (`grep -rn "172.20.20" tests --include=*.py`). Jakýkoli jiný červený test oprav teď. Zapiš si seznam červených testů — Task 3 je musí zezelenat.

- [ ] **Step 7: Commit**

```bash
git add migration_validator/models/inventory.py migration_validator/parsers/core.py tests/models/test_inventory.py tests/runs/test_services.py
git commit -m "feat(inventory): schema 10 - l3_interface in YAML, E-LAN local subtype"
```

---

### Task 3: Regenerace laboratorních inventory fixtures (schema 10)

**Files:**
- Regenerate: `tests/fixtures/172.20.20.4.yml`, `tests/fixtures/172.20.20.5.yml`, `172.20.20.4.yml`, `172.20.20.5.yml` (kořen)

**Interfaces:**
- Consumes: parser z Tasku 1–2 (`mx_parser.py`, `evo_parser.py`), laborka.
- Produces: inventory schema 10 s položkami `service_subtype: local` (MX `ge-0/0/2.10`, `ge-0/0/2.12`; PTX `et-0/0/8.10`, `et-0/0/8.12`) a `l3_interface`.

- [ ] **Step 1: Stav laborky**

```bash
eval "$(grep '^export MIG_LAB_PASSWORD=' ~/.bashrc)"
docker ps --format '{{.Names}}' | grep -E "MX1-POP1|PTX1-POP1"
```

Obě krabice musí mít globální domény **aktivní** (bez `inactive`). Ověř přes existující parser výstup v dalším kroku: pokud `routing_instance_active: false` u `local` položek, **zastav se a nahlas uživateli** — laborku nepřepínej sám.

- [ ] **Step 2: Vygeneruj inventory**

```bash
python3 mx_parser.py  172.20.20.4 --auth password -u admin -o tests/fixtures/172.20.20.4.yml
python3 evo_parser.py 172.20.20.5 --auth password -u admin -o tests/fixtures/172.20.20.5.yml
```

(Ověř přesné přepínače přes `python3 mx_parser.py --help`; heslo čte z `$MIG_LAB_PASSWORD` nebo se ptá.)

- [ ] **Step 3: Kontrola obsahu**

```bash
head -1 tests/fixtures/172.20.20.4.yml tests/fixtures/172.20.20.5.yml
grep -n -B2 -A3 "service_subtype: local" tests/fixtures/172.20.20.4.yml tests/fixtures/172.20.20.5.yml
grep -n -A1 "l3_interface" tests/fixtures/172.20.20.4.yml | head
```

Expected: `schema_version: 10`; MX `ge-0/0/2.10` a `ge-0/0/2.12` jsou `E-LAN / local` s `l3_interface: [irb.10]` resp. `[irb.2]`; PTX `et-0/0/8.10` / `et-0/0/8.12` totéž. `irb.2` / `irb.10` jsou `IPVPN / mvpn` s `l2_interface` na ten port. Pokud v laborce něco chybí (např. domény deaktivované), zastav se a nahlas.

- [ ] **Step 4: Kořenové ukázky**

```bash
cp tests/fixtures/172.20.20.4.yml 172.20.20.4.yml
cp tests/fixtures/172.20.20.5.yml 172.20.20.5.yml
```

- [ ] **Step 5: Celá sada**

Run: `.venv/bin/pytest`
Expected: PASS, včetně conformance testů. Pozn.: `tests/collectors/test_conformance.py::test_checks_produce_real_verdicts_not_all_skip` a spol. jedou nad **starými** RPC fixtures z `tests/fixtures/rpc/`; ty se nemění (default-switch v nich už je). Pokud conformance padne na tom, že nový `local` scope nemá v RPC fixtures data (jiné jméno domény/portu než v inventory), zapiš, který test a proč — a neupravuj fixtures ručně; nahlas uživateli.

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/172.20.20.4.yml tests/fixtures/172.20.20.5.yml 172.20.20.4.yml 172.20.20.5.yml
git commit -m "chore: regenerate lab inventory fixtures (schema 10, E-LAN local)"
```

---

### Task 4: Scope — důvod deaktivace a výběr `evpn_mac["default-switch"]`

**Files:**
- Modify: `migration_validator/models/scope.py:157-164` (`deactivation_reason`), `:230-234` (`select`, blok `evpn_mac`)
- Test: `tests/models/test_scope.py`

**Interfaces:**
- Produces: `Scope.deactivation_reason` vrací `"bridge-domain deactivated"` pro scope s `service_subtype == "local"` a bez `routing_instances`; `Scope.select(facts)["evpn_mac"]` pro takový scope obsahuje klíč `default-switch`, je-li ve faktech. Konstanta `LOCAL_L2_INSTANCE = "default-switch"` v `models/scope.py`.

- [ ] **Step 1: Failing testy**

Do `tests/models/test_scope.py` přidej:

```python
def _local_scope(active=True) -> Scope:
    return Scope(
        id="svc:NGMVPN-IGMP-RECEIVER:E-LAN",
        kind="service",
        key=ScopeKey("NGMVPN-IGMP-RECEIVER", "E-LAN", "local"),
        selectors=Selectors(
            interfaces=["ge-0/0/2.12"], vlans=["12"],
            bridge_domains=["BD-NGMVPN-IGMP-RECEIVER"],
        ),
        routing_instance_active=active,
    )


def test_local_scope_selects_default_switch_mac_table():
    facts = {
        "evpn_mac": {
            "default-switch": {"vlans": {"12": {"count": 1, "domain": "BD-X"}}, "interfaces": {}},
            "EVPN-VLAN-AWARE-POP1": {"vlans": {}, "interfaces": {}},
        }
    }
    selected = _local_scope().select(facts, {})
    assert list(selected["evpn_mac"]) == ["default-switch"]


def test_vlan_aware_scope_does_not_get_default_switch():
    scope = Scope(
        id="s", kind="service", key=ScopeKey("X", "E-LAN", "vlan-aware"),
        selectors=Selectors(interfaces=["ge-0/0/2.313"], routing_instances=["EVPN-A"]),
    )
    facts = {"evpn_mac": {"default-switch": {}, "EVPN-A": {}}}
    assert list(scope.select(facts, {})["evpn_mac"]) == ["EVPN-A"]


def test_local_scope_deactivation_reason_names_bridge_domain():
    assert _local_scope(active=False).deactivation_reason == "bridge-domain deactivated"
    off_both = _local_scope(active=False)
    off_both.interface_active = False
    assert off_both.deactivation_reason == "bridge-domain + interface deactivated"
```

- [ ] **Step 2: Ověř, že padají**

Run: `.venv/bin/pytest tests/models/test_scope.py -v -k "local_scope or vlan_aware_scope"`
Expected: FAIL — `[] != ["default-switch"]` a `"RI deactivated" != "bridge-domain deactivated"`.

- [ ] **Step 3: Implementace**

V `migration_validator/models/scope.py` přidej konstantu k ostatním (poblíž `MULTICAST_SUBTYPES`):

```python
# Systemova L2 instance boxu: globalni bridge-domains (MX) / vlans (EVO).
# Scope E-LAN `local` z ni cte MAC count; do routing_instances se nedava
# (matcher by dve lokalni domeny paroval jako jednu sluzbu).
LOCAL_L2_INSTANCE = "default-switch"
LOCAL_L2_SUBTYPE = "local"
```

`deactivation_reason`:

```python
    @property
    def deactivation_reason(self) -> str | None:
        """Kratky duvod do sloupce hodnot. None, kdyz je sluzba ziva."""
        reasons = []
        if not self.routing_instance_active:
            # E-LAN local nema RI - kontejnerem sluzby je bridge-domain.
            is_local = (
                self.service_subtype == LOCAL_L2_SUBTYPE
                and not self.selectors.routing_instances
            )
            reasons.append("bridge-domain" if is_local else "RI")
        if not self.interface_active:
            reasons.append("interface")
        return f"{' + '.join(reasons)} deactivated" if reasons else None
```

`select`, blok `evpn_mac`:

```python
        mac_instances = set(self.selectors.routing_instances)
        if self.service_type == "E-LAN" and self.service_subtype == LOCAL_L2_SUBTYPE:
            mac_instances.add(LOCAL_L2_INSTANCE)
        evpn_mac = {
            name: data
            for name, data in (facts.get("evpn_mac") or {}).items()
            if name in mac_instances
        }
```

- [ ] **Step 4: Testy + celá sada**

Run: `.venv/bin/pytest tests/models/test_scope.py -v` → PASS.
Run: `.venv/bin/pytest` → PASS.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/models/scope.py tests/models/test_scope.py
git commit -m "feat(scope): E-LAN local selects default-switch MAC table, bridge-domain deactivation reason"
```

---

### Task 5: Linker — konfigurační vazba `local` L2 → IRB

**Files:**
- Modify: `migration_validator/scoping/linker.py:64-121` (`link_scopes`)
- Test: `tests/scoping/test_linker.py`

**Interfaces:**
- Consumes: `Scope.selectors.l2_interfaces` (L3 scope, access porty IRB z inventory), `Scope.selectors.interfaces`, `Scope.selectors.routing_instances`, `ScopeLink`.
- Produces: `link_scopes(scopes, evpn_instance)` (signatura beze změny) vrací navíc `ScopeLink(l3_scope_id, l2_scope_id, irb_interface=<rozhraní L3 scope>, l2_interface=<rozhraní L2 scope>, l3_context=<RI L3 scope nebo "master">, l2_instance="default-switch")` pro každý E-LAN `local` scope s právě jedním L3 kandidátem.

- [ ] **Step 1: Failing testy**

Do `tests/scoping/test_linker.py` přidej:

```python
def _local_l2_scope(scope_id="svc:NGMVPN-IGMP-RECEIVER:E-LAN", interface="ge-0/0/2.12"):
    return Scope(
        id=scope_id,
        kind="service",
        key=ScopeKey("NGMVPN-IGMP-RECEIVER", "E-LAN", "local"),
        selectors=Selectors(interfaces=[interface], vlans=["12"]),
    )


def _irb_scope(
    scope_id="svc:NGMVPN receivers:IPVPN",
    interface="irb.2",
    instances=("NGMVPN-IGMP-RECEIVER",),
    l2_interfaces=("ge-0/0/2.12",),
):
    return Scope(
        id=scope_id,
        kind="service",
        key=ScopeKey("NGMVPN receivers", "IPVPN", "mvpn"),
        selectors=Selectors(
            interfaces=[interface],
            routing_instances=list(instances),
            l2_interfaces=list(l2_interfaces),
        ),
    )


def test_local_scope_links_to_irb_from_inventory_without_evpn_facts():
    l2, l3 = _local_l2_scope(), _irb_scope()
    assert link_scopes([l3, l2], {}) == [
        ScopeLink(
            l3_scope_id=l3.id,
            l2_scope_id=l2.id,
            irb_interface="irb.2",
            l2_interface="ge-0/0/2.12",
            l3_context="NGMVPN-IGMP-RECEIVER",
            l2_instance="default-switch",
        )
    ]


def test_local_scope_links_internet_irb_as_master():
    l2 = _local_l2_scope()
    l3 = _irb_scope(scope_id="svc:INET:Internet", instances=())
    l3.key = ScopeKey("INET", "Internet", None)
    (link,) = link_scopes([l3, l2], {})
    assert link.l3_context == "master"


def test_local_scope_with_two_irb_candidates_gets_no_link():
    l2 = _local_l2_scope()
    a = _irb_scope(scope_id="svc:A:IPVPN", interface="irb.2")
    b = _irb_scope(scope_id="svc:B:IPVPN", interface="irb.3")
    assert link_scopes([a, b, l2], {}) == []


def test_two_local_ports_link_to_one_irb():
    l2a = _local_l2_scope(scope_id="svc:A:E-LAN", interface="ge-0/0/2.12")
    l2b = _local_l2_scope(scope_id="svc:B:E-LAN", interface="ge-0/0/3.12")
    l3 = _irb_scope(l2_interfaces=("ge-0/0/2.12", "ge-0/0/3.12"))
    links = link_scopes([l3, l2a, l2b], {})
    assert {(l.l2_scope_id, l.l3_scope_id) for l in links} == {
        ("svc:A:E-LAN", l3.id), ("svc:B:E-LAN", l3.id)
    }


def test_local_port_not_listed_on_any_irb_gets_no_link():
    l2 = _local_l2_scope(interface="ge-0/0/9.12")
    assert link_scopes([_irb_scope(), l2], {}) == []


def test_vlan_aware_scope_ignores_l2_interfaces_source():
    # EVPN scope se vaze jen pres evpn_instance fakta - l2_interfaces na
    # IRB (z globalni domeny se stejnym portem) ho nesmi svazat.
    l2 = _l2_scope(interface="ge-0/0/2.12")
    l3 = _irb_scope()
    assert link_scopes([l3, l2], {}) == []
```

- [ ] **Step 2: Ověř, že padají**

Run: `.venv/bin/pytest tests/scoping/test_linker.py -v -k "local or vlan_aware_scope_ignores"`
Expected: FAIL — `[] != [ScopeLink(...)]` (tři z nich projdou triviálně, to je v pořádku).

- [ ] **Step 3: Implementace**

V `migration_validator/scoping/linker.py` doplň konstantu a pomocnou funkci, a v `link_scopes` volání:

```python
L3_SERVICE_TYPES = frozenset({"Internet", "IPVPN"})
L2_SERVICE_TYPE = "E-LAN"
LOCAL_L2_SUBTYPE = "local"
LOCAL_L2_INSTANCE = "default-switch"
```

```python
def _link_local_scopes(scopes: list[Scope]) -> list[ScopeLink]:
    """Vazba E-LAN `local` (globalni bridge-domain / vlan) na IRB.

    Zdrojem neni RPC vypis (default-switch zadny `l3_context` nema), ale
    inventory: IRB nese access porty svych domen v selektoru
    `l2_interfaces`. Pravidlo 'prave jeden kandidat' plati stejne jako
    u EVPN - spatny odkaz je horsi nez zadny (spec 2026-09-09).
    """
    l3_scopes = [
        scope
        for scope in scopes
        if not scope.is_device and scope.service_type in L3_SERVICE_TYPES
    ]
    links: list[ScopeLink] = []
    for scope in scopes:
        if scope.is_device or scope.service_type != L2_SERVICE_TYPE:
            continue
        if scope.service_subtype != LOCAL_L2_SUBTYPE or not scope.selectors.interfaces:
            continue
        l2_interface = scope.selectors.interfaces[0]
        candidates = [
            l3 for l3 in l3_scopes if l2_interface in l3.selectors.l2_interfaces
        ]
        if len(candidates) != 1:
            continue
        l3_scope = candidates[0]
        instances = l3_scope.selectors.routing_instances
        links.append(
            ScopeLink(
                l3_scope_id=l3_scope.id,
                l2_scope_id=scope.id,
                irb_interface=l3_scope.selectors.interfaces[0],
                l2_interface=l2_interface,
                l3_context=instances[0] if instances else "master",
                l2_instance=LOCAL_L2_INSTANCE,
            )
        )
    return links
```

Na konci `link_scopes` nahraď `return links` za:

```python
    links.extend(_link_local_scopes(scopes))
    return links
```

A ve stávající EVPN smyčce `link_scopes` přeskoč `local` scopy hned za testem na `L2_SERVICE_TYPE` (mají prázdné `routing_instances`, takže by stejně spadly na `continue`, ale explicitní podmínka drží záměr):

```python
        if scope.service_subtype == LOCAL_L2_SUBTYPE:
            continue
```

- [ ] **Step 4: Testy + celá sada**

Run: `.venv/bin/pytest tests/scoping/test_linker.py -v` → PASS.
Run: `.venv/bin/pytest` → PASS.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/scoping/linker.py tests/scoping/test_linker.py
git commit -m "feat(linker): config-based link from E-LAN local scope to its IRB"
```

---

### Task 6: Collector `evpn_mac` — `default-switch` se nezahazuje

**Files:**
- Modify: `migration_validator/collectors/evpn.py:252-254` (`SYSTEM_INSTANCES`)
- Test: `tests/collectors/test_evpn.py:103-108`

**Interfaces:**
- Produces: `EvpnMacCollector.parse(...)` vrací klíč `default-switch` se stejným tvarem `{"vlans": {...}, "interfaces": {...}}` jako ostatní instance.

- [ ] **Step 1: Přepiš test**

V `tests/collectors/test_evpn.py` nahraď `test_mac_count_skips_system_instance_and_empty_entries`:

```python
def test_mac_count_keeps_default_switch_and_skips_empty_entries(rpc_fixture):
    # default-switch = globalni bridge-domains / vlans (E-LAN local, spec
    # 2026-09-09). Drive se zahazoval jako systemova instance.
    result = EvpnMacCollector().parse(rpc_fixture("junos-evo", "evpn_mac"), "junos-evo")
    assert "default-switch" in result
    local = result["default-switch"]
    assert local["vlans"], "default-switch bez per-VLAN poctu"
    assert all(entry["domain"] for entry in local["vlans"].values())
    # prazdne <...-if-mac-count-entry/> bloky nesmi vyrobit zaznam
    aware = result["EVPN-VLAN-AWARE-CPE13-NNI"]
    assert set(aware["interfaces"]) == {"et-0/0/8.313"}


def test_mac_count_keeps_default_switch_on_mx(rpc_fixture):
    result = EvpnMacCollector().parse(rpc_fixture("junos", "evpn_mac"), "junos")
    assert "default-switch" in result
    assert result["default-switch"]["vlans"]
```

Pokud MX fixture `tests/fixtures/rpc/junos/evpn_mac.xml` nese v default-switch jen `BD-MUX1-B` (starší jméno), test na jméno domény nezávisí — kontroluje jen přítomnost počtů.

- [ ] **Step 2: Ověř, že padá**

Run: `.venv/bin/pytest tests/collectors/test_evpn.py -v -k default_switch`
Expected: FAIL — `assert "default-switch" in result`.

- [ ] **Step 3: Implementace**

V `migration_validator/collectors/evpn.py` nahraď komentář + `SYSTEM_INSTANCES` u `EvpnMacCollector`:

```python
    # default-switch (globalni bridge-domains / vlans) je od spec 2026-09-09
    # sluzba E-LAN `local`, ne systemova instance - nic se nezahazuje.
    # Zapis zustava jako mnozina, aby se dala pripadna vyjimka pridat bez
    # zmeny parse().
    SYSTEM_INSTANCES: frozenset[str] = frozenset()
```

- [ ] **Step 4: Testy + celá sada**

Run: `.venv/bin/pytest tests/collectors -v` → PASS.
Run: `.venv/bin/pytest` → PASS. Conformance test `test_specific_check_sees_data[junos-evo-evpn_mac_count]` musí zůstat zelený; pokud device scope report v jiném testu porovnává přesný počet řádků `evpn_mac`, uprav očekávání (default-switch teď řádky přidává).

- [ ] **Step 5: Commit**

```bash
git add migration_validator/collectors/evpn.py tests/collectors/test_evpn.py
git commit -m "feat(collector): keep default-switch in MAC count facts"
```

---

### Task 7: Checky — vyloučení EVPN checků pro `local`, label `MAC count`, syntetická fakta

**Files:**
- Modify: `migration_validator/checks/evpn.py:325-332` (`EvpnEsiStatusCheck`), `:549-557` (`EvpnInstanceStatusCheck`), `:849-856` (`EvpnMacCountCheck.label`)
- Modify: `tests/conftest.py:455-490` (`_facts_for`, větev E-LAN)
- Test: `tests/checks/test_evpn.py`, `tests/checks/test_base.py` (nebo tam, kde se testuje `applies_to`)

**Interfaces:**
- Produces: `EvpnEsiStatusCheck.excluded_subtypes == EvpnInstanceStatusCheck.excluded_subtypes == frozenset({"local"})`; `EvpnMacCountCheck.label == "MAC count"`; syntetický snapshot pro scope E-LAN `local` nese `evpn_mac["default-switch"]`.

- [ ] **Step 1: Failing testy**

Do `tests/checks/test_evpn.py` přidej:

```python
def _local_ctx(subject, baseline=None):
    ctx = _ctx(subject, baseline, subtype="local")
    ctx.scope.selectors.interfaces = ["ge-0/0/2.12"]
    ctx.scope.selectors.routing_instances = []
    ctx.scope.selectors.vlans = ["12"]
    return ctx


def _local_mac_subject():
    return {"evpn_mac": {"default-switch": {
        "vlans": {
            "12": {"domain": "BD-NGMVPN-IGMP-RECEIVER", "count": 1},
            "10": {"domain": "BD-NGMVPN-PIM-RECEIVER", "count": 2},
        },
        "interfaces": {
            "ge-0/0/2.12": {"name": "ge-0/0/2.12:12", "domain": "BD-NGMVPN-IGMP-RECEIVER", "count": 1},
            "ge-0/0/2.10": {"name": "ge-0/0/2.10:10", "domain": "BD-NGMVPN-PIM-RECEIVER", "count": 2},
        },
    }}}


def test_local_mac_count_shows_only_its_own_domain_and_port():
    findings = EvpnMacCountCheck().run(_local_ctx(_local_mac_subject()))
    labels = [f.label for f in findings]
    assert labels == [
        "BD-NGMVPN-IGMP-RECEIVER MAC count",
        "BD-NGMVPN-IGMP-RECEIVER Interface ge-0/0/2.12:12 MAC count",
    ]
    assert all(f.outcome is Outcome.OK for f in findings)


def test_local_mac_count_compares_against_baseline():
    baseline = _local_mac_subject()
    baseline["evpn_mac"]["default-switch"]["vlans"]["12"]["count"] = 5
    findings = EvpnMacCountCheck().run(_local_ctx(_local_mac_subject(), baseline))
    row = _by_label(findings, "BD-NGMVPN-IGMP-RECEIVER MAC count")
    assert row.baseline_value == "5"


def test_mac_count_label_is_generic():
    assert EvpnMacCountCheck.label == "MAC count"
    assert EvpnMacCountCheck.id == "evpn_mac_count"


def test_evpn_only_checks_skip_local_subtype():
    local = _local_ctx({}).scope
    aware = _ctx({}).scope
    for check in (EvpnEsiStatusCheck(), EvpnInstanceStatusCheck()):
        assert check.applies_to(local) is False
        assert check.applies_to(aware) is True
    assert EvpnMacCountCheck().applies_to(local) is True
```

Pokud v souboru už existuje test, který asertuje label `"EVPN MAC count"`, změň ho na `"MAC count"` (hledej `grep -rn "EVPN MAC count" tests migration_validator`).

- [ ] **Step 2: Ověř, že padají**

Run: `.venv/bin/pytest tests/checks/test_evpn.py -v -k "local or label_is_generic"`
Expected: FAIL — label `"EVPN MAC count"`, `applies_to(local)` je `True` u ESI/instance checku.

- [ ] **Step 3: Implementace checků**

V `migration_validator/checks/evpn.py`:

`EvpnEsiStatusCheck` — za `service_types = frozenset({"E-LAN"})`:

```python
    # E-LAN local (globalni bridge-domain, spec 2026-09-09) nema ESI ani
    # EVPN instanci - bez vylouceni by kazdy lokalni blok nesl SKIP "bez dat".
    excluded_subtypes = frozenset({"local"})
```

`EvpnInstanceStatusCheck` — totéž za jeho `service_types = frozenset({"E-LAN"})`.

`EvpnMacCountCheck` — `label = "MAC count"` (docstring/komentář: label je společný pro vlan-aware, vlan-based i local; id `evpn_mac_count` zůstává kvůli profilům).

- [ ] **Step 4: Syntetická fakta v conftest**

V `tests/conftest.py`, větev `if service_type == "E-LAN" and instance:` uprav tak, aby `local` scope bez instance dostal `default-switch`:

```python
        if service_type == "E-LAN" and instance is None and scope.key.service_subtype == "local":
            # E-LAN local: MAC count zije v default-switch (spec 2026-09-09),
            # ESI ani EVPN instance neexistuji.
            instance = "default-switch"
        if service_type == "E-LAN" and instance:
            if instance != "default-switch":
                evpn_esi[f"esi-{instance}"] = {
                    "status": "Up",
                    "df_role": "DF",
                    "interface": scope.selectors.interfaces[0],
                }
            ... (stávající kód plnění evpn_mac beze změny)
```

Tj. stávající blok `evpn_esi[...] = {...}` se obalí podmínkou `if instance != "default-switch":`, plnění `evpn_mac` zůstává společné.

- [ ] **Step 5: End-to-end test nad syntetickým snapshotem**

Do `tests/test_end_to_end.py` (nebo `tests/test_engine.py`, podle toho, kde se používá fixture `synthetic_snapshot` s inventory záznamy) přidej test, který postaví inventory se dvěma záznamy — IRB (`IPVPN`, `l2_interface: ["ge-0/0/2.12"]`) a portem (`E-LAN`/`local`, `l3_interface: ["irb.2"]`, `customer_vlan: ["12"]`, `bridge_domain: ["BD-X"]`) — vyhodnotí ho přes `api.evaluate` a ověří:

```python
def test_local_elan_block_follows_its_irb_block(...):
    result = api.evaluate(snapshot, now=NOW)
    ids = [scope.scope_id for scope in result.scopes if scope.kind == "service"]
    irb_index = ids.index("svc:NGMVPN receivers:IPVPN")
    assert ids[irb_index + 1] == "svc:NGMVPN-IGMP-RECEIVER:E-LAN"
    local = result.scopes[irb_index + 1]
    check_ids = {check.id for check in local.checks}
    assert "evpn_mac_count" in check_ids
    assert "evpn_esi_status" not in check_ids
    assert "evpn_instance_status" not in check_ids
    assert local.link["peer_interface"] == "irb.2"
```

Přesná jména atributů (`scope_id`, `kind`, `checks`, `link`) ověř v `migration_validator/models/result.py` a v existujících testech téhož souboru; použij stejné helpery, jaké tam už stavějí snapshot z inventory (hledej `synthetic_snapshot`, `_snapshot(`).

- [ ] **Step 6: Testy + celá sada**

Run: `.venv/bin/pytest tests/checks/test_evpn.py tests/test_end_to_end.py tests/test_engine.py -v` → PASS.
Run: `.venv/bin/pytest` → PASS.

- [ ] **Step 7: Commit**

```bash
git add migration_validator/checks/evpn.py tests/checks/test_evpn.py tests/conftest.py tests/test_end_to_end.py tests/test_engine.py
git commit -m "feat(checks): E-LAN local - generic MAC count label, EVPN-only checks excluded"
```

---

### Task 8: Dokumentace

**Files:**
- Modify: `docs/cs/files/parsers.md` (sekce „Kde se ty dva soubory liší" tabulka + nová podsekce), `docs/cs/files/models.md` (tabulka `ServiceEntry`, changelog 9 → 10), `docs/en/files/parsers.md`, `docs/en/files/models.md` (totéž anglicky)

- [ ] **Step 1: parsers.md (cs)**

Před sekci `## Kde se ty dva soubory liší` vlož:

```markdown
## E-LAN `local`: globální bridge-domain / vlan (vlna 2026-09-09)

Access port bez routing-instance, který je explicitně (`interface/name`) členem
globální `bridge-domains/domain` (MX) nebo `vlans/vlan` (EVO) — tedy instance
`default-switch` — se klasifikuje jako **E-LAN / `local`** (confidence high, důvod
„Rozhraní je členem globální bridge-domain / vlan (default-switch), bez EVPN instance.").
Odlišnost od `vlan-aware` je chybějící EVPN/MPLS transport: L2 je lokální na boxu
a opouští ho jen přes IRB (`routing-interface` na MX, `l3-interface` na EVO).

- Vyhledání domény je jen podle explicitního členství, nikdy podle překryvu VLAN —
  `default-switch` na EVO nese i `default` (VLAN 1).
- `bridge_domain`, `customer_vlan` a `l3_interface` (irb protějšky) se plní stejně jako
  u instančních domén; `routing_instance` je `null`.
- `routing_instance_active` nese aktivitu **domény** (`BridgeDomain.active`, dědí se
  z kontejneru `bridge-domains inactive` / `vlans inactive`) — doména je kontejner služby
  stejně jako RI u EVPN. IRB tím dotčena není.
- L2 port bez domény zůstává `Unknown / layer2`.

Runtime: scope `local` čte MAC count z `evpn_mac["default-switch"]`, vazba na IRB
vzniká z inventory (selektor `l2_interfaces` IRB), EVPN-only checky (`evpn_esi_status`,
`evpn_instance_status`) se na `local` neaplikují. Viz spec
`docs/superpowers/specs/2026-09-09-local-bridge-domain-elan-design.md`.
```

V tabulce „Kde se ty dva soubory liší" řádek `E-LAN subtype` doplň na konci obou buněk: „; `local` = globální doména bez instance (společné)".

- [ ] **Step 2: models.md (cs)**

Do tabulky `ServiceEntry` za řádek `customer_vlan` (resp. před `l2_interface`) přidej:

```markdown
| `l3_interface` | `list[str]` | (schema 10) irb protějšky bridge-domains/vlanů tohoto L2 unitu (`routing-interface` / `l3-interface`); per-port filtr inventory podle nich přitahuje L3 polovinu služby |
```

Řádek `service_subtype` rozšiř o `local`. Do changelogu za „8 → 9" přidej:

```markdown
- **9 → 10** (spec 2026-09-09): subtype E-LAN `local` (access port v globální
  bridge-domain / vlan, instance `default-switch`) a pole `l3_interface` se poprvé
  zapisuje do YAML. Stará inventory by port nesla jako `Unknown/layer2` — žádný scope,
  žádný blok v reportu, a per-port běh by nepřitáhl IRB polovinu služby.
```

- [ ] **Step 3: Anglické protějšky**

Totéž v `docs/en/files/parsers.md` a `docs/en/files/models.md` (sekce „E-LAN `local`: global bridge-domain / vlan (2026-09-09 wave)", řádek `l3_interface`, changelog „9 → 10").

- [ ] **Step 4: Commit**

```bash
git add docs/cs/files/parsers.md docs/cs/files/models.md docs/en/files/parsers.md docs/en/files/models.md
git commit -m "docs: E-LAN local subtype, l3_interface field, inventory schema 10"
```

---

### Task 9: Ověření v laborce (per-port běhy, aktivní i deaktivované domény)

**Files:**
- žádné commitované změny (výstupy do `runs/local-bd/`, `runs/` je v `.gitignore` — ověř `git check-ignore runs/local-bd`)

- [ ] **Step 1: Heslo a stav**

```bash
eval "$(grep '^export MIG_LAB_PASSWORD=' ~/.bashrc)"
docker ps --format '{{.Names}}' | grep -E "MX1-POP1|PTX1-POP1"
```

- [ ] **Step 2: Per-port pre/post capture**

Přesné přepínače `capture` viz `mig-validate capture --help` (`--run`, `--port`, `--parse-services`, `--phase`, `--username`, `--password`). Cílem je run s pre snapshotem z MX1-POP1 portu `ge-0/0/2` a post snapshotem z PTX1-POP1 portu `et-0/0/8`, stejně jako v `runs-example/migration-pop1/run.yml` (tam je vzor argumentů).

```bash
mig-validate capture --device 172.20.20.4 --run local-bd --port ge-0/0/2 --parse-services \
    --phase pre-migration --username admin --password "$MIG_LAB_PASSWORD"
mig-validate capture --device 172.20.20.5 --run local-bd --port et-0/0/8 --parse-services \
    --maps-to 172.20.20.4:ge-0/0/2 --phase post-migration --username admin --password "$MIG_LAB_PASSWORD"
mig-validate evaluate --run local-bd
```

- [ ] **Step 3: Kontrola inventory**

`runs/local-bd/inventory_MX1-POP1_ge_0_0_2.yml`: `ge-0/0/2.10` a `ge-0/0/2.12` jsou `E-LAN / local` s `l3_interface`; `irb.2` a `irb.10` jsou v inventory (`IPVPN / mvpn`). Totéž pro PTX `et-0/0/8`.

- [ ] **Step 4: Kontrola reportu**

Očekávané bloky (per každou ze dvou služeb): blok IRB (IPVPN / mvpn, poznámka `L2: ge-0/0/2.12` v hlavičce, multicast řádky) a hned za ním blok `E-LAN / local (L2 část)` s řádky `BD-… MAC count`, `BD-… Interface … MAC count`, `Interface status`, `Interface errors`, `Interface traffic`. Žádný řádek `EVPN ESI status`, `EVPN instance`, ani `IRB interface` na L2 bloku. MAC count porovnaný s baseline přes přejmenovaný port (`ge-0/0/2.12` → `et-0/0/8.12`).

- [ ] **Step 5: Deaktivovaný stav**

Požádej uživatele, aby v laborce deaktivoval `bridge-domains` na MX1-POP1 nebo `vlans` na PTX1-POP1 (laborku nepřepínej sám), a zopakuj post capture do nového runu (`--run local-bd-off`). Očekávání: L2 blok má `Deaktivace` = `bridge-domain deactivated` (BROKEN proti živé baseline), ostatní checky L2 bloku SKIP; blok IRB hlásí naměřený stav (oper down / multicast BROKEN), ne odvozený.

- [ ] **Step 6: Zápis**

Každý rozdíl proti očekávání je buď chyba implementace (oprav + test + commit do příslušného tasku), nebo nová informace z laborky — zapiš ji do spec sekce „Uzavřená rozhodnutí" jako ověřený fakt s datem. Poté `superpowers:finishing-a-development-branch`.
